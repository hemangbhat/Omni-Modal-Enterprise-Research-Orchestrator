"""Integration test: retriever uses pool, not psycopg.connect.
Feature: performance-and-scalability
Validates: Requirements 3.1
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

import _path
from omni_modal.qa.retrieval import PgVectorChunkRetriever
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.models import QueryRequest


class TestRetrieverUsesPool(unittest.TestCase):
    """Retriever uses ConnectionPool instead of psycopg.connect — Validates: Req 3.1"""

    def test_retriever_uses_pool_connection_not_direct_connect(self):
        """When pool is injected, pool.connection() is called; psycopg.connect is not."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        retriever = PgVectorChunkRetriever(
            embedding_provider=HashingQueryEmbeddingProvider(),
            pool=mock_pool,
        )

        request = QueryRequest(
            tenant_id="t1",
            user_id="u1",
            question="What is the market outlook?",
            top_k=5,
            min_similarity=0.0,
            stream=False,
        )

        # Build a mock psycopg module so patching works even without psycopg installed
        mock_direct_connect = MagicMock()
        mock_psycopg = MagicMock()
        mock_psycopg.connect = mock_direct_connect

        # Patch sys.modules to inject the mock psycopg, and patch observability
        with patch.dict("sys.modules", {"psycopg": mock_psycopg, "psycopg.rows": MagicMock()}), \
             patch("omni_modal.qa.retrieval.observability"):
            try:
                retriever.retrieve(request)
            except Exception:
                pass  # may fail without a real DB; we only care what was called

        # pool.connection() must have been called (pool path taken)
        mock_pool.connection.assert_called()
        # psycopg.connect must NOT have been called (direct connect path NOT taken)
        mock_direct_connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
