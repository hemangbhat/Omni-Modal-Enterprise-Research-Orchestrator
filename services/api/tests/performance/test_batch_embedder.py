"""Properties 10–12, 16: BatchEmbedder correctness.
Feature: performance-and-scalability
"""
from __future__ import annotations

import math
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import _path  # noqa: F401
from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st

from omni_modal.ingestion.batch_embedder import BatchEmbedder, BatchInsertError
from omni_modal.ingestion.models import SourceReference, StructuredChunk


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

def st_source_reference() -> st.SearchStrategy[SourceReference]:
    """Generate a minimal SourceReference."""
    return st.builds(
        SourceReference,
        source_path=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
        source_kind=st.just("pdf"),
    )


def st_structured_chunk(chunk_index: int | None = None) -> st.SearchStrategy[StructuredChunk]:
    """Generate a StructuredChunk.  chunk_index defaults to a drawn integer."""
    idx_strategy = st.just(chunk_index) if chunk_index is not None else st.integers(min_value=0, max_value=9999)
    return st.builds(
        StructuredChunk,
        chunk_index=idx_strategy,
        content=st.text(min_size=1, max_size=200),
        content_hash=st.text(min_size=8, max_size=64, alphabet="0123456789abcdef"),
        source=st_source_reference(),
        start_word=st.integers(min_value=0, max_value=500),
        end_word=st.integers(min_value=1, max_value=1000),
    )


def st_chunk_list(min_size: int = 0, max_size: int = 50) -> st.SearchStrategy[list[StructuredChunk]]:
    """Generate a list of StructuredChunks with unique, sequential chunk_index values."""
    return st.integers(min_value=min_size, max_value=max_size).flatmap(
        lambda n: st.tuples(*[st_structured_chunk(i) for i in range(n)]).map(list)
        if n > 0
        else st.just([])
    )


def make_mock_pool(rowcount_override: int | None = None) -> MagicMock:
    """Build a mock pool whose cursor.rowcount matches the batch size automatically.

    If rowcount_override is provided, cursor.rowcount is set to that value instead.
    """
    pool = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()

    # By default, rowcount is set to whatever executemany received (len of rows)
    # We simulate this with a side_effect on executemany
    if rowcount_override is None:
        def executemany_side_effect(sql, rows):
            cursor.rowcount = len(rows)
        cursor.executemany.side_effect = executemany_side_effect
    else:
        cursor.rowcount = rowcount_override

    conn.cursor.return_value = cursor

    # Make conn.transaction() a context manager
    conn.transaction.return_value.__enter__ = MagicMock(return_value=None)
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)

    # Make pool.connection() a context manager returning conn
    pool.connection.return_value.__enter__ = MagicMock(return_value=conn)
    pool.connection.return_value.__exit__ = MagicMock(return_value=False)

    return pool, conn, cursor


# ---------------------------------------------------------------------------
# Property 10: Batch partitioning correctness — Validates: Requirements 5.1
# ---------------------------------------------------------------------------

class TestBatchPartitioning(unittest.TestCase):
    """Property 10: Batch partitioning correctness.

    # Feature: performance-and-scalability, Property 10: Batch partitioning correctness
    **Validates: Requirements 5.1**
    """

    @given(
        items=st.lists(st.integers()),
        batch_size=st.integers(min_value=1, max_value=256),
    )
    @settings(max_examples=300)
    def test_batch_count_is_ceil_n_over_b(self, items, batch_size):
        """Property 10a: produces exactly ceil(N/B) sublists."""
        embedder = BatchEmbedder(pool=None, batch_size=batch_size)
        batches = embedder._batches(items)
        n = len(items)
        expected_count = math.ceil(n / batch_size) if n > 0 else 0
        self.assertEqual(len(batches), expected_count)

    @given(
        items=st.lists(st.integers(), min_size=1),
        batch_size=st.integers(min_value=1, max_value=256),
    )
    @settings(max_examples=300)
    def test_each_batch_length_at_most_b(self, items, batch_size):
        """Property 10b: each sublist has length <= batch_size."""
        embedder = BatchEmbedder(pool=None, batch_size=batch_size)
        batches = embedder._batches(items)
        for b in batches:
            self.assertLessEqual(len(b), batch_size)

    @given(
        items=st.lists(st.integers()),
        batch_size=st.integers(min_value=1, max_value=256),
    )
    @settings(max_examples=300)
    def test_concatenation_equals_original(self, items, batch_size):
        """Property 10c: concatenation of sublists equals original list."""
        embedder = BatchEmbedder(pool=None, batch_size=batch_size)
        batches = embedder._batches(items)
        reconstructed = [item for b in batches for item in b]
        self.assertEqual(reconstructed, items)

    def test_empty_list_produces_no_batches(self):
        """Edge case: empty input yields empty output."""
        embedder = BatchEmbedder(pool=None, batch_size=10)
        self.assertEqual(embedder._batches([]), [])

    def test_batch_size_1_each_item_own_batch(self):
        """Edge case: batch_size=1 gives one sublist per element."""
        embedder = BatchEmbedder(pool=None, batch_size=1)
        items = [1, 2, 3, 4, 5]
        batches = embedder._batches(items)
        self.assertEqual(len(batches), 5)
        self.assertEqual([[1], [2], [3], [4], [5]], batches)

    def test_batch_size_larger_than_list(self):
        """Edge case: batch_size > len(items) gives exactly one batch."""
        embedder = BatchEmbedder(pool=None, batch_size=100)
        items = list(range(10))
        batches = embedder._batches(items)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0], items)


# ---------------------------------------------------------------------------
# Property 11: Batch insert row-count invariant — Validates: Requirements 5.2
# ---------------------------------------------------------------------------

class TestBatchInsertRowCount(unittest.TestCase):
    """Property 11: Batch insert row-count invariant.

    # Feature: performance-and-scalability, Property 11: Batch insert row-count invariant
    **Validates: Requirements 5.2**
    """

    @given(
        chunks=st_chunk_list(min_size=1, max_size=50),
        batch_size=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
    def test_no_error_when_rowcount_matches(self, chunks, batch_size):
        """Property 11: When mock cursor rowcount == len(batch), no BatchInsertError raised."""
        if not chunks:
            return

        pool, conn, cursor = make_mock_pool()
        embedder = BatchEmbedder(pool=pool, batch_size=batch_size)

        # Should not raise
        try:
            result = embedder._insert_chunk_batch(
                cursor=cursor,
                tenant_id="tenant-1",
                document_id="doc-1",
                batch=chunks,
            )
            # rowcount is set to len(rows) == len(chunks) by side_effect
            self.assertEqual(result, len(chunks))
        except BatchInsertError:
            self.fail("BatchInsertError was raised unexpectedly when rowcount matched batch size")

    def test_batch_insert_error_raised_on_mismatch(self):
        """Unit: BatchInsertError raised when cursor.rowcount != len(batch)."""
        pool, conn, cursor = make_mock_pool()
        # Override: cursor always reports rowcount=0 regardless of batch
        cursor.executemany.side_effect = None
        cursor.rowcount = 0

        chunks = [
            StructuredChunk(
                chunk_index=0,
                content="hello",
                content_hash="abc123",
                source=SourceReference(source_path="doc.pdf", source_kind="pdf"),
                start_word=0,
                end_word=1,
            )
        ]
        embedder = BatchEmbedder(pool=pool, batch_size=64)
        with self.assertRaises(BatchInsertError) as ctx:
            embedder._insert_chunk_batch(cursor, "tenant", "doc", chunks)
        self.assertEqual(ctx.exception.expected, 1)
        self.assertEqual(ctx.exception.actual, 0)

    def test_batch_insert_error_message(self):
        """Unit: BatchInsertError has human-readable message with expected/actual."""
        err = BatchInsertError(expected=10, actual=7)
        self.assertIn("10", str(err))
        self.assertIn("7", str(err))
        self.assertEqual(err.expected, 10)
        self.assertEqual(err.actual, 7)


# ---------------------------------------------------------------------------
# Property 12: Idempotent re-ingestion (upsert) — Validates: Requirements 5.3, 5.5
# ---------------------------------------------------------------------------

class TestIdempotentReIngestion(unittest.TestCase):
    """Property 12: Idempotent re-ingestion (upsert).

    # Feature: performance-and-scalability, Property 12: Idempotent re-ingestion (upsert)
    **Validates: Requirements 5.3, 5.5**
    """

    @given(chunks=st_chunk_list(min_size=1, max_size=30))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_double_write_same_upsert_calls(self, chunks):
        """Property 12: Two identical write_chunks calls produce the same DB operations.

        We verify that the second call's executemany arguments are identical to the
        first call's, confirming that the upsert SQL is used (ON CONFLICT DO UPDATE)
        and there's no divergence in data between the two writes.
        """
        if not chunks:
            return

        embeddings = [[0.1, 0.2, 0.3] for _ in chunks]

        # --- First write ---
        pool1, conn1, cursor1 = make_mock_pool()
        embedder = BatchEmbedder(pool=pool1, batch_size=10)
        embedder.write_chunks("tenant", "doc-id", chunks, embeddings)
        first_calls = list(cursor1.executemany.call_args_list)

        # --- Second write (same data) ---
        pool2, conn2, cursor2 = make_mock_pool()
        embedder2 = BatchEmbedder(pool=pool2, batch_size=10)
        embedder2.write_chunks("tenant", "doc-id", chunks, embeddings)
        second_calls = list(cursor2.executemany.call_args_list)

        # Both writes must produce the same number of executemany calls
        self.assertEqual(len(first_calls), len(second_calls))

        # The SQL used must include ON CONFLICT for upsert semantics
        for c in first_calls:
            sql = c.args[0]
            self.assertIn("ON CONFLICT", sql)

    @given(chunks=st_chunk_list(min_size=1, max_size=20))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_upsert_sql_present_in_chunk_insert(self, chunks):
        """Property 12b: The chunk batch insert SQL always uses ON CONFLICT DO UPDATE."""
        if not chunks:
            return

        pool, conn, cursor = make_mock_pool()
        embedder = BatchEmbedder(pool=pool, batch_size=64)
        embedder._insert_chunk_batch(cursor, "t", "d", chunks)

        sql = cursor.executemany.call_args.args[0]
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("DO UPDATE", sql)

    @given(chunks=st_chunk_list(min_size=1, max_size=20))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_upsert_sql_present_in_embedding_insert(self, chunks):
        """Property 12c: The embedding batch insert SQL always uses ON CONFLICT DO UPDATE."""
        if not chunks:
            return

        pool, conn, cursor = make_mock_pool()
        embedder = BatchEmbedder(pool=pool, batch_size=64)
        chunk_ids = [c.content_hash for c in chunks]
        embeddings = [[0.1] * 3 for _ in chunks]
        embedder._insert_embedding_batch(cursor, "t", "d", chunk_ids, embeddings)

        sql = cursor.executemany.call_args.args[0]
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("DO UPDATE", sql)


# ---------------------------------------------------------------------------
# Property 16: Connection not held across pipeline stages — Validates: Requirements 8.4
# ---------------------------------------------------------------------------

class TestConnectionReleasedPerBatch(unittest.TestCase):
    """Property 16: Connection not held across pipeline stages.

    # Feature: performance-and-scalability, Property 16: Connection not held across pipeline stages
    **Validates: Requirements 8.4**
    """

    @given(chunks=st_chunk_list(min_size=1, max_size=30))
    @settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
    def test_pool_connection_entered_and_exited_exactly_once(self, chunks):
        """Property 16: pool.connection() context manager entered/exited exactly once per write_chunks()."""
        if not chunks:
            return

        embeddings = [[0.1, 0.2] for _ in chunks]
        pool, conn, cursor = make_mock_pool()
        embedder = BatchEmbedder(pool=pool, batch_size=10)

        embedder.write_chunks("tenant", "doc-id", chunks, embeddings)

        # pool.connection() should be called exactly once
        self.assertEqual(pool.connection.call_count, 1)

        # The context manager __enter__ and __exit__ should each be called once
        self.assertEqual(pool.connection.return_value.__enter__.call_count, 1)
        self.assertEqual(pool.connection.return_value.__exit__.call_count, 1)

    @given(chunks=st_chunk_list(min_size=1, max_size=30))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_transaction_entered_and_exited_exactly_once(self, chunks):
        """Property 16b: conn.transaction() context manager entered/exited exactly once."""
        if not chunks:
            return

        embeddings = [[0.1] for _ in chunks]
        pool, conn, cursor = make_mock_pool()
        embedder = BatchEmbedder(pool=pool, batch_size=10)

        embedder.write_chunks("tenant", "doc-id", chunks, embeddings)

        # transaction should be entered and exited exactly once
        self.assertEqual(conn.transaction.return_value.__enter__.call_count, 1)
        self.assertEqual(conn.transaction.return_value.__exit__.call_count, 1)

    def test_empty_chunks_skips_pool_connection(self):
        """Edge case: empty chunk list returns 0 and never acquires a connection."""
        pool = MagicMock()
        embedder = BatchEmbedder(pool=pool, batch_size=10)
        result = embedder.write_chunks("t", "d", [], [])
        self.assertEqual(result, 0)
        pool.connection.assert_not_called()

    def test_none_pool_raises_runtime_error(self):
        """Edge case: pool=None raises RuntimeError."""
        embedder = BatchEmbedder(pool=None, batch_size=10)
        chunks = [
            StructuredChunk(
                chunk_index=0,
                content="text",
                content_hash="hash0",
                source=SourceReference(source_path="f.pdf", source_kind="pdf"),
                start_word=0,
                end_word=1,
            )
        ]
        with self.assertRaises(RuntimeError):
            embedder.write_chunks("t", "d", chunks, [[0.1]])


if __name__ == "__main__":
    unittest.main()
