"""Research limited to a set of documents (a project), and the cache that must respect it."""

from __future__ import annotations

import unittest
from unittest import mock

import _path  # noqa: F401

from omni_modal.qa.cache import QueryCache
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.in_memory_store import InMemoryChunkRetriever, InMemoryVectorStore, StoredChunk
from omni_modal.qa.models import QueryRequest
from omni_modal.qa.retrieval import PgVectorChunkRetriever


def _store() -> tuple[InMemoryVectorStore, HashingQueryEmbeddingProvider]:
    provider = HashingQueryEmbeddingProvider()
    store = InMemoryVectorStore()
    for doc, text in (("d1", "Revenue grew 18 percent."), ("d2", "Revenue fell 3 percent."), ("d3", "Revenue flat.")):
        store.add(StoredChunk(tenant_id="t1", document_id=doc, title=doc, source_type="pdf", chunk_id=f"{doc}:0",
                              chunk_index=0, content=text, embedding=provider.embed_query(text)))
    return store, provider


class InMemoryScopeTests(unittest.TestCase):
    def test_scope_limits_results_to_the_listed_documents(self) -> None:
        store, provider = _store()
        retriever = InMemoryChunkRetriever(provider, store)
        got = retriever.retrieve(QueryRequest("t1", "u1", "revenue", document_ids=("d2",)))
        self.assertEqual([c.document_id for c in got], ["d2"])

    def test_an_empty_scope_returns_nothing(self) -> None:
        store, provider = _store()
        got = InMemoryChunkRetriever(provider, store).retrieve(QueryRequest("t1", "u1", "revenue", document_ids=()))
        self.assertEqual(got, [])

    def test_unsearchable_documents_are_skipped(self) -> None:
        store, provider = _store()
        retriever = InMemoryChunkRetriever(provider, store)
        retriever.is_searchable = lambda tenant_id, document_id: document_id != "d1"  # d1 archived
        got = retriever.retrieve(QueryRequest("t1", "u1", "revenue"))
        self.assertNotIn("d1", [c.document_id for c in got])


class CacheScopeTests(unittest.TestCase):
    def test_cache_key_depends_on_scope(self) -> None:
        everything = QueryCache.compute_key("revenue", "t1", 5, 0.0)
        scoped = QueryCache.compute_key("revenue", "t1", 5, 0.0, document_ids=("d2",))
        self.assertNotEqual(everything, scoped)
        self.assertEqual(scoped, QueryCache.compute_key("revenue", "t1", 5, 0.0, document_ids=["d2"]))
        self.assertEqual(
            QueryCache.compute_key("q", "t", 5, 0.0, document_ids=("a", "b")),
            QueryCache.compute_key("q", "t", 5, 0.0, document_ids=("b", "a")),
        )


class PgScopeTests(unittest.TestCase):
    def test_sql_filters_by_scope_and_archive(self) -> None:
        executed: list[tuple[str, tuple]] = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def execute(self, sql, params):
                executed.append((sql, params))

            def fetchall(self):
                return []

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def cursor(self, **kw):
                return _Cursor()

        class _Pool:
            def connection(self):
                return _Conn()

        retriever = PgVectorChunkRetriever(HashingQueryEmbeddingProvider(), pool=_Pool())
        with mock.patch.dict("os.environ", {"RLS_ENFORCEMENT": "false"}):
            retriever.retrieve(QueryRequest("t1", "u1", "revenue", document_ids=("11111111-1111-1111-1111-111111111111",)))
        sql, params = executed[-1]
        self.assertIn("d.archived_at is null", sql)
        self.assertIn("d.id = any(%s::uuid[])", sql)
        self.assertIn(["11111111-1111-1111-1111-111111111111"], params)


if __name__ == "__main__":
    unittest.main()
