from __future__ import annotations

import hashlib
import os
from typing import Protocol

from omni_modal.qa.embeddings import QueryEmbeddingProvider
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
    ) -> None:
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
            )
            cached = self._cache.get(_cache_key)
            if cached is not None:
                return cached

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

            vector_literal = "[" + ",".join(str(value) for value in embedding) + "]"

            # --- Search sub-span ---
            with observability.child_span("retrieval.search", "execute vector search"):
                try:
                    if self._pool is not None:
                        _conn_ctx = self._pool.connection()
                    else:
                        _conn_ctx = psycopg.connect(self._database_url, row_factory=dict_row)
                    with _conn_ctx as connection:
                        if self._pool is not None:
                            # pool connections need row_factory set on the cursor
                            from psycopg.rows import dict_row as _dict_row  # noqa: PLC0415
                            _cursor_ctx = connection.cursor(row_factory=_dict_row)
                        else:
                            _cursor_ctx = connection.cursor()
                        with _cursor_ctx as cursor:
                            cursor.execute(
                                """
                                select
                                  c.id as chunk_id,
                                  d.id as document_id,
                                  d.title,
                                  d.source_type,
                                  c.chunk_index,
                                  c.content,
                                  1 - (e.embedding <=> %s::vector) as similarity,
                                  c.metadata
                                from embeddings e
                                inner join document_chunks c on c.id = e.chunk_id
                                inner join documents d on d.id = e.document_id
                                where e.tenant_id = %s
                                  and d.status = 'ready'
                                  and (%s = 0 or 1 - (e.embedding <=> %s::vector) >= %s)
                                order by e.embedding <=> %s::vector
                                limit %s
                                """,
                                (
                                    vector_literal,
                                    request.tenant_id,
                                    request.min_similarity,
                                    vector_literal,
                                    request.min_similarity,
                                    vector_literal,
                                    request.top_k,
                                ),
                            )
                            rows = cursor.fetchall()
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

                return [
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
