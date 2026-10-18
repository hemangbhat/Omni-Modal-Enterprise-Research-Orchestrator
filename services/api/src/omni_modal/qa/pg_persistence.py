"""Postgres + pgvector ingestion persistence (Phase C).

Writes a ready ``IngestionResult`` end-to-end into the real schema:
``users`` → ``documents`` → ``document_chunks`` → ``embeddings`` (vector).
Chunk text is embedded with the injected provider (e.g. bge-small, 384-dim),
so the pgvector retriever can perform genuine semantic search over ingested
content.

Idempotent: re-ingesting the same ``document_id`` replaces its chunks and
embeddings inside a single transaction. Satisfies the ``ChunkPersistence``
protocol (``persist(result) -> int``).
"""

from __future__ import annotations

import json
import uuid

from omni_modal.ingestion.models import IngestionResult
from omni_modal.qa.embeddings import EmbeddingProvider

# document_source_type enum values that exist in the schema.
_VALID_SOURCE_TYPES = {"pdf", "audio", "transcript", "note", "web"}


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in embedding) + "]"


class DocumentGone(Exception):
    """The document was deleted while it was being processed."""


def ensure_user_row(cur, tenant_id: str, owner_id: str) -> str:
    """The ``users`` row id for an auth user, created on first use (documents.owner_id needs one)."""
    email = owner_id if "@" in owner_id else f"{owner_id}@omero.local"
    cur.execute(
        """INSERT INTO users (tenant_id, email, display_name, role)
           VALUES (%s, %s, %s, 'researcher')
           ON CONFLICT (tenant_id, email)
           DO UPDATE SET display_name = EXCLUDED.display_name
           RETURNING id""",
        (tenant_id, email, owner_id),
    )
    return str(cur.fetchone()[0])


class PostgresChunkPersistence:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        *,
        pool=None,
        database_url: str | None = None,
        embedding_model: str = "sentence-transformers",
        dimensions: int = 384,
        require_document: bool = False,
    ) -> None:
        # True for the durable upload flow, where the document row is created at
        # upload time: persisting then only updates that row, under a lock, and
        # refuses if it was deleted meanwhile (instead of re-creating it).
        self._require_document = require_document
        self._provider = embedding_provider
        self._pool = pool
        self._database_url = database_url
        self._embedding_model = embedding_model
        self._dimensions = dimensions

    def _connect(self):
        if self._pool is not None:
            return self._pool.connection()
        import psycopg  # noqa: PLC0415

        return psycopg.connect(self._database_url)

    def persist(self, result: IngestionResult) -> int:
        if not result.chunks:
            return 0

        contents = [c.content for c in result.chunks]
        vectors = self._provider.embed_documents(contents)
        if len(vectors) != len(result.chunks):
            raise RuntimeError(
                f"Embedding count {len(vectors)} != chunk count {len(result.chunks)}"
            )

        source_type = result.source_kind if result.source_kind in _VALID_SOURCE_TYPES else "note"

        with self._connect() as conn:
            with conn.transaction():
                from omni_modal.db.rls import apply_tenant  # noqa: PLC0415

                apply_tenant(conn, result.tenant_id)
                with conn.cursor() as cur:
                    if self._require_document:
                        cur.execute(
                            """SELECT id FROM documents WHERE id = %s AND tenant_id = %s FOR UPDATE""",
                            (result.document_id, result.tenant_id),
                        )
                        if cur.fetchone() is None:
                            raise DocumentGone(result.document_id)
                        cur.execute(
                            """UPDATE documents SET title = %s, status = 'ready', processed_at = now(),
                                      updated_at = now()
                               WHERE id = %s AND tenant_id = %s""",
                            (result.title, result.document_id, result.tenant_id),
                        )
                    else:
                        owner_uuid = self._ensure_user(cur, result.tenant_id, result.owner_id)
                        self._upsert_document(cur, result, owner_uuid, source_type)
                    # Re-ingest: drop existing chunks (embeddings cascade) then insert fresh.
                    cur.execute(
                        "DELETE FROM document_chunks WHERE document_id = %s",
                        (result.document_id,),
                    )
                    for chunk, vector in zip(result.chunks, vectors):
                        chunk_uuid = str(uuid.uuid4())
                        cur.execute(
                            """INSERT INTO document_chunks
                               (id, tenant_id, document_id, chunk_index, content, content_hash, metadata)
                               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
                            (
                                chunk_uuid, result.tenant_id, result.document_id,
                                chunk.chunk_index, chunk.content, chunk.content_hash,
                                json.dumps(chunk.metadata or {}),
                            ),
                        )
                        cur.execute(
                            """INSERT INTO embeddings
                               (id, tenant_id, document_id, chunk_id, embedding, embedding_model, dimensions)
                               VALUES (%s, %s, %s, %s, %s::vector, %s, %s)""",
                            (
                                str(uuid.uuid4()), result.tenant_id, result.document_id,
                                chunk_uuid, _vector_literal(vector),
                                self._embedding_model, self._dimensions,
                            ),
                        )
        return len(result.chunks)

    def delete_document_chunks(self, tenant_id: str, document_id: str) -> int:
        """Remove a document's chunks (embeddings cascade): used when processing
        fails, so a failed document never answers from an older version."""
        with self._connect() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM document_chunks WHERE document_id::text = %s AND tenant_id = %s",
                    (document_id, tenant_id),
                )
                return cur.rowcount

    def _ensure_user(self, cur, tenant_id: str, owner_id: str) -> str:
        return ensure_user_row(cur, tenant_id, owner_id)

    def _upsert_document(self, cur, result: IngestionResult, owner_uuid: str, source_type: str) -> None:
        cur.execute(
            """INSERT INTO documents (id, tenant_id, owner_id, title, source_type, status, processed_at)
               VALUES (%s, %s, %s, %s, %s::document_source_type, 'ready', now())
               ON CONFLICT (id)
               DO UPDATE SET title = EXCLUDED.title, status = 'ready', processed_at = now()""",
            (result.document_id, result.tenant_id, owner_uuid, result.title, source_type),
        )
