"""BatchEmbedder: writes StructuredChunk rows to document_chunks + embeddings in batches.

Feature: performance-and-scalability
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # psycopg_pool types only used in hints

from omni_modal.ingestion.models import StructuredChunk


class BatchInsertError(Exception):
    """Raised when the number of rows affected does not match the expected batch size."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Batch insert row-count mismatch: expected {expected}, got {actual}."
        )
        self.expected = expected
        self.actual = actual


class BatchEmbedder:
    """Writes StructuredChunk rows to document_chunks + embeddings in configurable-size batches.

    All batches for a single document are written inside one transaction.
    Uses INSERT … ON CONFLICT DO UPDATE for idempotent re-ingestion.

    If psycopg_pool is not installed (or pool is None), falls back to no-op/raises.
    """

    def __init__(
        self,
        pool,  # psycopg_pool.ConnectionPool | None
        *,
        batch_size: int = 64,
        embedding_model: str = "hashing-placeholder",
        dimensions: int = 1536,
    ) -> None:
        self._pool = pool
        self._batch_size = max(1, batch_size)
        self._embedding_model = embedding_model
        self._dimensions = dimensions

    def _batches(self, items: Sequence) -> list[list]:
        """Split items into sublists of at most batch_size elements.

        Property 10: For any list of N items and batch_size B ≥ 1, produces ceil(N/B)
        sublists, each of length <= B, and their concatenation equals the original list.
        """
        n = len(items)
        if n == 0:
            return []
        return [
            list(items[i : i + self._batch_size])
            for i in range(0, n, self._batch_size)
        ]

    def write_chunks(
        self,
        tenant_id: str,
        document_id: str,
        chunks: Sequence[StructuredChunk],
        embeddings: Sequence[list[float]],
    ) -> int:
        """Write all chunks + embeddings within a single transaction.

        Returns the total number of rows inserted/updated across all batches.
        Raises BatchInsertError on row-count mismatch.
        Rolls back the entire transaction on any error.

        NOTE: This is a STUB implementation — it simulates the DB operations
        using mock-compatible logic. In production this would use psycopg pool.
        """
        if not chunks:
            return 0
        if self._pool is None:
            raise RuntimeError("BatchEmbedder requires a ConnectionPool.")

        total = 0
        with self._pool.connection() as conn:
            with conn.transaction():
                # Write chunks in batches
                for batch in self._batches(chunks):
                    affected = self._insert_chunk_batch(
                        conn.cursor(), tenant_id, document_id, batch
                    )
                    total += affected

                # Write embeddings in batches
                chunk_ids = [c.content_hash for c in chunks]  # use content_hash as stable ID
                emb_batches = self._batches(list(zip(chunk_ids, embeddings)))
                for batch in emb_batches:
                    batch_chunk_ids = [pair[0] for pair in batch]
                    batch_embs = [pair[1] for pair in batch]
                    self._insert_embedding_batch(
                        conn.cursor(), tenant_id, document_id, batch_chunk_ids, batch_embs
                    )

        return total

    def _insert_chunk_batch(
        self,
        cursor,
        tenant_id: str,
        document_id: str,
        batch: list[StructuredChunk],
    ) -> int:
        """Execute upsert for one chunk batch; return rowcount.

        SQL:
            INSERT INTO document_chunks
              (id, tenant_id, document_id, chunk_index, content, content_hash, metadata)
            VALUES %s
            ON CONFLICT (document_id, chunk_index) DO UPDATE
              SET content      = EXCLUDED.content,
                  content_hash = EXCLUDED.content_hash,
                  metadata     = EXCLUDED.metadata
        """
        rows = [
            (
                c.content_hash,  # stable deterministic ID
                tenant_id,
                document_id,
                c.chunk_index,
                c.content,
                c.content_hash,
                str(c.metadata),
            )
            for c in batch
        ]
        cursor.executemany(
            """
            INSERT INTO document_chunks
              (id, tenant_id, document_id, chunk_index, content, content_hash, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id, chunk_index) DO UPDATE
              SET content      = EXCLUDED.content,
                  content_hash = EXCLUDED.content_hash,
                  metadata     = EXCLUDED.metadata
            """,
            rows,
        )
        rowcount = getattr(cursor, "rowcount", len(batch))
        if rowcount != len(batch):
            raise BatchInsertError(expected=len(batch), actual=rowcount)
        return rowcount

    def _vector_literal(self, embedding: list[float]) -> str:
        """Format an embedding as a pgvector text literal: ``[v1,v2,...]``."""
        return "[" + ",".join(repr(float(v)) for v in embedding) + "]"

    def _insert_embedding_batch(
        self,
        cursor,
        tenant_id: str,
        document_id: str,
        chunk_ids: list[str],
        batch: list[list[float]],
    ) -> int:
        """Execute upsert for one embedding batch; return rowcount.

        SQL:
            INSERT INTO embeddings
              (id, tenant_id, document_id, chunk_id, embedding, embedding_model, dimensions)
            VALUES %s
            ON CONFLICT (chunk_id) DO UPDATE
              SET embedding       = EXCLUDED.embedding,
                  embedding_model = EXCLUDED.embedding_model
        """
        rows = [
            (
                str(uuid.uuid4()),
                tenant_id,
                document_id,
                chunk_id,
                self._vector_literal(emb),  # pgvector text literal: "[v1,v2,...]"
                self._embedding_model,
                self._dimensions,
            )
            for chunk_id, emb in zip(chunk_ids, batch)
        ]
        cursor.executemany(
            """
            INSERT INTO embeddings
              (id, tenant_id, document_id, chunk_id, embedding, embedding_model, dimensions)
            VALUES (%s, %s, %s, %s, %s::vector, %s, %s)
            ON CONFLICT (chunk_id) DO UPDATE
              SET embedding       = EXCLUDED.embedding,
                  embedding_model = EXCLUDED.embedding_model
            """,
            rows,
        )
        rowcount = getattr(cursor, "rowcount", len(rows))
        if rowcount != len(rows):
            raise BatchInsertError(expected=len(rows), actual=rowcount)
        return rowcount
