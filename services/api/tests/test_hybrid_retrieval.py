"""Hybrid retrieval: BM25, rank fusion, the in-memory retriever and the Postgres query shape."""

from __future__ import annotations

import unittest
from unittest import mock

import _path  # noqa: F401

from omni_modal.qa.cache import QueryCache
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.in_memory_store import InMemoryChunkRetriever, InMemoryVectorStore, StoredChunk
from omni_modal.qa.lexical import (
    bm25_scores,
    reciprocal_rank_fusion,
    retrieval_mode,
    stem,
    tsquery_text,
)
from omni_modal.qa.models import QueryRequest
from omni_modal.qa.retrieval import PgVectorChunkRetriever


class _ZeroProvider:
    """Every vector is the same, so vector ranking carries no signal."""

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


def _store(texts: list[str], provider) -> InMemoryVectorStore:
    store = InMemoryVectorStore()
    for i, text in enumerate(texts):
        store.add(StoredChunk("t1", f"d{i}", f"Doc {i}", "pdf", f"d{i}:0", 0, text, provider.embed_query(text)))
    return store


class LexicalTests(unittest.TestCase):
    def test_mode_defaults_and_rejects_unknown(self) -> None:
        self.assertEqual(retrieval_mode({}), "hybrid")
        self.assertEqual(retrieval_mode({"RETRIEVAL_MODE": "Vector"}), "vector")
        self.assertEqual(retrieval_mode({"RETRIEVAL_MODE": "nonsense"}), "hybrid")

    def test_stem_joins_word_forms(self) -> None:
        self.assertEqual(stem("approved"), stem("approves"))
        self.assertEqual(stem("renewals"), stem("renewal"))

    def test_bm25_prefers_rare_matching_terms(self) -> None:
        docs = ["the vendor contract renews in march", "the vendor is reliable", "lunch menu for friday"]
        scores = bm25_scores("When does the vendor contract renew?", docs)
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(scores[2], 0.0)
        self.assertEqual(bm25_scores("the of and", docs), [0.0, 0.0, 0.0])

    def test_rrf_rewards_agreement(self) -> None:
        fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "x", "y"]])
        self.assertEqual(fused[0], "b")
        self.assertEqual(set(fused), {"a", "b", "c", "x", "y"})

    def test_tsquery_is_safe_or_query(self) -> None:
        self.assertEqual(tsquery_text("What's the Q3 budget? & | ! :*"), "budget | q3")
        self.assertIsNone(tsquery_text("the of !!"))


class InMemoryHybridTests(unittest.TestCase):
    def test_hybrid_finds_keyword_match_vector_misses(self) -> None:
        texts = ["General notes about the office.", "Project Zephyr budget is 2.4 million.", "Holiday schedule."]
        provider = _ZeroProvider()
        store = _store(texts, provider)
        req = QueryRequest("t1", "u1", "Zephyr budget", top_k=1)
        hybrid = InMemoryChunkRetriever(provider, store, mode="hybrid").retrieve(req)
        self.assertEqual(hybrid[0].document_id, "d1")
        self.assertAlmostEqual(hybrid[0].similarity, 1.0)  # still the cosine score

    def test_hybrid_respects_scope_tenant_and_searchable(self) -> None:
        provider = HashingQueryEmbeddingProvider()
        store = _store(["Zephyr budget approved.", "Zephyr budget rejected."], provider)
        store.add(StoredChunk("t2", "x", "Other", "pdf", "x:0", 0, "Zephyr budget", provider.embed_query("Zephyr")))
        retriever = InMemoryChunkRetriever(provider, store, mode="hybrid")
        retriever.is_searchable = lambda tenant, doc: doc != "d0"
        out = retriever.retrieve(QueryRequest("t1", "u1", "Zephyr budget", top_k=5))
        self.assertEqual([c.document_id for c in out], ["d1"])
        scoped = InMemoryChunkRetriever(provider, store, mode="hybrid")
        out = scoped.retrieve(QueryRequest("t1", "u1", "Zephyr", top_k=5, document_ids=("d0",)))
        self.assertEqual([c.document_id for c in out], ["d0"])
        self.assertEqual(scoped.retrieve(QueryRequest("t1", "u1", "Zephyr", document_ids=())), [])

    def test_vector_mode_unchanged(self) -> None:
        provider = HashingQueryEmbeddingProvider()
        store = _store(["alpha beta", "gamma delta"], provider)
        out = InMemoryChunkRetriever(provider, store, mode="vector").retrieve(QueryRequest("t1", "u1", "alpha beta"))
        self.assertEqual(out[0].document_id, "d0")


class _Recorder:
    def __init__(self, results: list[list[dict]]) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._results = results

    def pool(self):
        recorder = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def execute(self, sql, params):
                recorder.executed.append((sql, params))

            def fetchall(self):
                return recorder._results.pop(0) if recorder._results else []

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

        return _Pool()


def _row(cid: str) -> dict:
    return {"chunk_id": cid, "document_id": "11111111-1111-1111-1111-111111111111", "title": "T",
            "source_type": "pdf", "chunk_index": 0, "content": cid, "similarity": 0.5, "metadata": {}}


class PostgresHybridTests(unittest.TestCase):
    def test_hybrid_runs_filtered_keyword_query_and_fuses(self) -> None:
        rec = _Recorder([[_row("v1"), _row("both")], [_row("both"), _row("k1")]])
        retriever = PgVectorChunkRetriever(HashingQueryEmbeddingProvider(), pool=rec.pool(), mode="hybrid")
        with mock.patch.dict("os.environ", {"RLS_ENFORCEMENT": "false"}):
            out = retriever.retrieve(QueryRequest("t1", "u1", "Zephyr budget", top_k=2))
        self.assertEqual([c.chunk_id for c in out], ["both", "v1"])
        keyword_sql, params = rec.executed[1]
        for clause in ("e.tenant_id = %s", "d.archived_at is null", "d.status = 'ready'",
                       "to_tsquery('english', %s)", "any(%s::uuid[])"):
            self.assertIn(clause, keyword_sql)
        self.assertIn("budget | zephyr", params)
        self.assertIn("t1", params)

    def test_vector_mode_runs_one_query(self) -> None:
        rec = _Recorder([[_row("v1")]])
        retriever = PgVectorChunkRetriever(HashingQueryEmbeddingProvider(), pool=rec.pool(), mode="vector")
        with mock.patch.dict("os.environ", {"RLS_ENFORCEMENT": "false"}):
            retriever.retrieve(QueryRequest("t1", "u1", "Zephyr budget", top_k=3))
        self.assertEqual(len(rec.executed), 1)
        self.assertEqual(rec.executed[0][1][-1], 3)

    def test_results_are_cached(self) -> None:
        rec = _Recorder([[_row("v1")], [_row("v1")]])
        cache = QueryCache()
        retriever = PgVectorChunkRetriever(HashingQueryEmbeddingProvider(), pool=rec.pool(), cache=cache)
        with mock.patch.dict("os.environ", {"RLS_ENFORCEMENT": "false"}):
            first = retriever.retrieve(QueryRequest("t1", "u1", "Zephyr", top_k=3))
            calls = len(rec.executed)
            second = retriever.retrieve(QueryRequest("t1", "u1", "Zephyr", top_k=3))
        self.assertEqual(len(rec.executed), calls)
        self.assertEqual([c.chunk_id for c in first], [c.chunk_id for c in second])
        cache.evict_tenant("t1")
        self.assertIsNone(cache.get(QueryCache.compute_key("Zephyr", "t1", 3, 0.0, None)))


if __name__ == "__main__":
    unittest.main()
