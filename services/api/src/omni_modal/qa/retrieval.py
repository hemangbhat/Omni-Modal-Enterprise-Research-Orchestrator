from __future__ import annotations

import hashlib
import os
from typing import Protocol

from omni_modal.qa.embeddings import QueryEmbeddingProvider
from omni_modal.qa.fastembed_provider import EmbeddingUnavailable
from omni_modal.qa.lexical import candidate_depth, reciprocal_rank_fusion, retrieval_mode, tsquery_text
from omni_modal.qa.models import QueryRequest, RetrievedChunk
from omni_modal.observability import observability


def _classify_retrieval_error(exc: BaseException) -> str:
    """Classify a retrieval exception into a failure category string.

    Returns ``"connection_error"`` for connection/timeout related failures,
    and ``"query_error"`` for all other exceptions.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return "connection_error"
    class_name = type(exc).__name__.lower()
    if "connection" in class_name:
        return "connection_error"
    return "query_error"


class ChunkRetriever(Protocol):
    def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
        raise NotImplementedError


class PgVectorChunkRetriever:
    def __init__(
        self,
        embedding_provider: QueryEmbeddingProvider,
        database_url: str | None = None,
        *,
        pool: "ConnectionPool | None" = None,
        cache: "QueryCache | None" = None,
        mode: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.mode = mode or retrieval_mode()
        # Vector search only compares vectors made by this model (None: any).
        self.model_id = model_id
        self._embedding_provider = embedding_provider
        self._database_url = database_url or os.environ.get("DATABASE_URL")
        self._pool = pool
        self._cache = cache

    def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
        # --- Cache check ---
        _cache_key: str | None = None
        if self._cache is not None:
            from omni_modal.qa.cache import QueryCache
            _cache_key = QueryCache.compute_key(
                request.question,
                request.tenant_id,
                request.top_k,
                request.min_similarity,
                request.document_ids,
            )
            cached = self._cache.get(_cache_key)
            if cached is not None:
                return cached

        if request.document_ids is not None and not request.document_ids:
            return []  # an empty scope (a project with no documents) matches nothing

        if not self._pool and not self._database_url:
            error = RuntimeError("DATABASE_URL is required for pgvector retrieval.")
            observability.capture_exception(
                error,
                operation="retrieval.pgvector.configuration",
                context={"tenant_id": request.tenant_id, "top_k": request.top_k},
            )
            raise error

        try:
            import psycopg  # type: ignore[import-not-found]
            from psycopg.rows import dict_row  # type: ignore[import-not-found]
        except ImportError as exc:
            observability.capture_exception(
                exc,
                operation="retrieval.pgvector.import",
                context={
                    "tenant_id": request.tenant_id,
                    "top_k": request.top_k,
                },
            )
            raise RuntimeError(
                "pgvector retrieval requires local psycopg installation."
            ) from exc

        with observability.child_span("retrieval", "vector similarity search"):
            # --- Embed sub-span ---
            with observability.child_span("retrieval.embed", "generate query embedding"):
                try:
                    embedding = self._embedding_provider.embed_query(request.question)
                except EmbeddingUnavailable as exc:
                    # The model could not load or run: rank by keywords alone.
                    observability.capture_exception(exc, operation="retrieval.embedding.degraded")
                    embedding = None
                except Exception as exc:
                    observability.capture_exception(
                        exc,
                        operation="retrieval.embedding",
                        context={
                            "query_length": len(request.question),
                            "embedding_model": type(self._embedding_provider).__name__,
                            "error_category": "embedding_error",
                        },
                    )
                    raise

            vector_literal = None if embedding is None else "[" + ",".join(str(value) for value in embedding) + "]"
            scope = None if request.document_ids is None else list(request.document_ids)

            # --- Search sub-span ---
            with observability.child_span("retrieval.search", "execute vector search"):
                try:
                    if self._pool is not None:
                        _conn_ctx = self._pool.connection()
                    else:
                        _conn_ctx = psycopg.connect(self._database_url, row_factory=dict_row)
                    with _conn_ctx as connection:
                        # Optional DB-enforced tenant isolation (RLS). No-op
                        # unless RLS_ENFORCEMENT=true; uses a txn-local GUC so a
                        # pooled connection never leaks one tenant's binding.
                        import contextlib  # noqa: PLC0415

                        from omni_modal.db.rls import apply_tenant, rls_enabled  # noqa: PLC0415

                        _use_txn = self._pool is not None and rls_enabled()
                        _txn_ctx = connection.transaction() if _use_txn else contextlib.nullcontext()
                        with _txn_ctx:
                            if _use_txn:
                                apply_tenant(connection, request.tenant_id)
                            if self._pool is not None:
                                # pool connections need row_factory set on the cursor
                                from psycopg.rows import dict_row as _dict_row  # noqa: PLC0415
                                _cursor_ctx = connection.cursor(row_factory=_dict_row)
                            else:
                                _cursor_ctx = connection.cursor()
                            with _cursor_ctx as cursor:
                                rows = self._search(cursor, request, vector_literal, scope)
                except Exception as exc:
                    query_hash = hashlib.md5(request.question.encode()).hexdigest()
                    failure_classification = _classify_retrieval_error(exc)
                    observability.capture_exception(
                        exc,
                        operation="retrieval.pgvector.query",
                        context={
                            "tenant_id": request.tenant_id,
                            "query_hash": query_hash,
                            "top_k": request.top_k,
                            "failure_classification": failure_classification,
                            "min_similarity": request.min_similarity,
                        },
                    )
                    raise

            # --- Rank sub-span ---
            with observability.child_span("retrieval.rank", "rank and format results"):
                if not rows:
                    observability.add_breadcrumb(
                        message="Retrieval returned zero results",
                        category="retrieval",
                        level="info",
                        data={
                            "query_length": len(request.question),
                            "embedding_model": type(self._embedding_provider).__name__,
                            "top_k": request.top_k,
                            "min_similarity": request.min_similarity,
                        },
                    )

                results = [
                    RetrievedChunk(
                        chunk_id=str(row["chunk_id"]),
                        document_id=str(row["document_id"]),
                        title=str(row["title"]),
                        source_type=str(row["source_type"]),
                        chunk_index=int(row["chunk_index"]),
                        content=str(row["content"]),
                        similarity=float(row["similarity"]),
                        metadata=dict(row["metadata"] or {}),
                    )
                    for row in rows
                ]
                if self._cache is not None and _cache_key is not None:
                    self._cache.set(_cache_key, results, tenant_id=request.tenant_id)
                return results

    _FROM = """
        from embeddings e
        inner join document_chunks c on c.id = e.chunk_id
        inner join documents d on d.id = e.document_id
        where e.tenant_id = %s
          and d.status = 'ready'
          and d.archived_at is null
          and (%s::uuid[] is null or d.id = any(%s::uuid[]))
    """
    _SELECT = "c.id as chunk_id, d.id as document_id, d.title, d.source_type, c.chunk_index, c.content, c.metadata"

    def _search(self, cursor, request: QueryRequest, vector_literal: str | None, scope) -> list[dict]:
        """The vector ranking, fused with the full-text ranking in hybrid mode.

        With no query vector (the embedding model is unavailable) only the
        full-text ranking runs, so search degrades instead of failing. Vector
        search compares only vectors from the current model; full-text search
        covers every chunk, so documents not yet re-embedded stay findable.
        """
        where = (request.tenant_id, scope, scope)
        has_terms = tsquery_text(request.question) is not None
        hybrid = self.mode == "hybrid" and has_terms
        limit = candidate_depth(request.top_k) if hybrid else request.top_k
        vector_rows: list[dict] = []
        if vector_literal is not None:
            cursor.execute(
                f"""select {self._SELECT}, 1 - (e.embedding <=> %s::vector) as similarity {self._FROM}
                      and (%s::text is null or e.embedding_model = %s)
                      and (%s = 0 or 1 - (e.embedding <=> %s::vector) >= %s)
                    order by e.embedding <=> %s::vector limit %s""",
                (vector_literal, *where, self.model_id, self.model_id, request.min_similarity,
                 vector_literal, request.min_similarity, vector_literal, limit),
            )
            vector_rows = cursor.fetchall()
            if not hybrid:
                return vector_rows
        elif not has_terms:
            return []
        tsquery = tsquery_text(request.question)
        if vector_literal is not None:
            similarity, sim_params = "1 - (e.embedding <=> %s::vector)", (vector_literal,)
        else:
            similarity, sim_params = "0.0", ()
        cursor.execute(
            f"""select {self._SELECT}, {similarity} as similarity {self._FROM}
                  and to_tsvector('english', c.content) @@ to_tsquery('english', %s)
                order by ts_rank_cd(to_tsvector('english', c.content), to_tsquery('english', %s)) desc
                limit %s""",
            (*sim_params, *where, tsquery, tsquery, candidate_depth(request.top_k)),
        )
        keyword_rows = cursor.fetchall()
        if vector_literal is None:
            return keyword_rows[: max(0, request.top_k)]
        by_id = {str(r["chunk_id"]): r for r in [*vector_rows, *keyword_rows]}
        ranked = reciprocal_rank_fusion([
            [str(r["chunk_id"]) for r in vector_rows], [str(r["chunk_id"]) for r in keyword_rows],
        ])
        return [by_id[cid] for cid in ranked[: max(0, request.top_k)]]
