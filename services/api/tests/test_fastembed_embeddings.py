"""Hugging Face embeddings in the API: lazy loading, keyword fallback, model filtering, re-embedding."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import _path  # noqa: F401

from omni_modal.qa.embedding_factory import select_embedding_provider
from omni_modal.qa.fastembed_provider import EmbeddingUnavailable, FastEmbedProvider
from omni_modal.qa.in_memory_store import InMemoryChunkRetriever, InMemoryVectorStore, StoredChunk
from omni_modal.qa.models import QueryRequest
from omni_modal.qa.retrieval import PgVectorChunkRetriever


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def query_embed(self, texts):
        self.calls.append(("query", None))
        return iter([[1.0, 0.0, 0.0] for _ in texts])

    def embed(self, texts, batch_size=None):
        self.calls.append(("docs", batch_size))
        return iter([[0.0, 1.0, 0.0] for _ in texts])


class ProviderTests(unittest.TestCase):
    def test_loads_on_first_use_and_uses_query_instruction(self) -> None:
        loads = []
        model = _FakeModel()
        provider = FastEmbedProvider("BAAI/bge-small-en-v1.5", loader=lambda name: loads.append(name) or model)
        self.assertFalse(provider.loaded)
        self.assertEqual(loads, [])
        self.assertEqual(provider.embed_query("who owns it?"), [1.0, 0.0, 0.0])
        self.assertEqual(provider.embed_documents(["a", "b"]), [[0.0, 1.0, 0.0]] * 2)
        self.assertEqual(loads, ["BAAI/bge-small-en-v1.5"])
        self.assertEqual(model.calls, [("query", None), ("docs", 4)])
        self.assertEqual(provider.model_id, "fastembed/BAAI/bge-small-en-v1.5")

    def test_load_failure_is_reported_not_raised_raw(self) -> None:
        def broken(name):
            raise MemoryError("no room")

        provider = FastEmbedProvider(loader=broken)
        with self.assertRaises(EmbeddingUnavailable):
            provider.embed_query("x")
        self.assertIn("MemoryError", provider.last_error)

    def test_factory_selects_fastembed_or_falls_back(self) -> None:
        selection = select_embedding_provider(env={"EMBEDDING_BACKEND": "fastembed"})
        try:
            import fastembed  # noqa: F401
        except ImportError:
            self.assertTrue(selection.fell_back)
        else:
            self.assertEqual(selection.backend, "fastembed")
            self.assertEqual(selection.model_id, "fastembed/BAAI/bge-small-en-v1.5")
            self.assertTrue(selection.is_semantic)
        with mock.patch.dict(sys.modules, {"fastembed": None}):
            fallback = select_embedding_provider(env={"EMBEDDING_BACKEND": "fastembed"})
        self.assertTrue(fallback.fell_back)
        self.assertEqual(fallback.model_id, "hashing")


class _Unavailable:
    def embed_query(self, text):
        raise EmbeddingUnavailable("model missing")


class KeywordFallbackTests(unittest.TestCase):
    def test_in_memory_search_falls_back_to_keywords(self) -> None:
        store = InMemoryVectorStore()
        for doc, text in (("a", "The Nimbus contract renews in March."), ("b", "Lunch menu for Friday.")):
            store.add(StoredChunk("t1", doc, doc, "pdf", f"{doc}:0", 0, text, [0.0, 1.0, 0.0]))
        for mode in ("vector", "hybrid"):
            out = InMemoryChunkRetriever(_Unavailable(), store, mode=mode).retrieve(
                QueryRequest("t1", "u", "When does the Nimbus contract renew?"))
            self.assertEqual([c.document_id for c in out], ["a"], mode)
            self.assertEqual(out[0].similarity, 0.0)


class _Recorder:
    def __init__(self, results):
        self.executed, self._results = [], list(results)

    def pool(self):
        rec = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def execute(self, sql, params):
                rec.executed.append((sql, params))

            def fetchall(self):
                return rec._results.pop(0) if rec._results else []

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def cursor(self, **kw):
                return _Cur()

        class _Pool:
            def connection(self):
                return _Conn()

        return _Pool()


def _row(cid):
    return {"chunk_id": cid, "document_id": "11111111-1111-1111-1111-111111111111", "title": "T",
            "source_type": "pdf", "chunk_index": 0, "content": cid, "similarity": 0.0, "metadata": {}}


class PostgresTests(unittest.TestCase):
    def test_vector_query_filters_by_model(self) -> None:
        from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider  # noqa: PLC0415

        rec = _Recorder([[_row("v")], [_row("k")]])
        retriever = PgVectorChunkRetriever(HashingQueryEmbeddingProvider(dimensions=8), pool=rec.pool(),
                                           mode="hybrid", model_id="fastembed/BAAI/bge-small-en-v1.5")
        with mock.patch.dict("os.environ", {"RLS_ENFORCEMENT": "false"}):
            retriever.retrieve(QueryRequest("t1", "u", "Nimbus contract", top_k=2))
        vector_sql, vector_params = rec.executed[0]
        self.assertIn("e.embedding_model = %s", vector_sql)
        self.assertIn("fastembed/BAAI/bge-small-en-v1.5", vector_params)
        keyword_sql, _ = rec.executed[1]
        self.assertNotIn("embedding_model", keyword_sql)  # not-yet-re-embedded chunks stay findable

    def test_unavailable_model_runs_keyword_query_only(self) -> None:
        rec = _Recorder([[_row("k1"), _row("k2")]])
        retriever = PgVectorChunkRetriever(_Unavailable(), pool=rec.pool(), mode="hybrid", model_id="m")
        with mock.patch.dict("os.environ", {"RLS_ENFORCEMENT": "false"}):
            out = retriever.retrieve(QueryRequest("t1", "u", "Nimbus contract", top_k=1))
        self.assertEqual(len(rec.executed), 1)
        sql, params = rec.executed[0]
        self.assertIn("0.0 as similarity", sql)
        self.assertIn("to_tsquery", sql)
        self.assertEqual([c.chunk_id for c in out], ["k1"])


class ReembedTests(unittest.TestCase):
    def test_updates_only_other_models_in_batches(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
        import reembed  # noqa: PLC0415

        batches = [[(1, "alpha"), (2, "beta")], [(3, "gamma")], []]
        updates, commits = [], []

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def execute(self, sql, params):
                if sql.startswith("UPDATE"):
                    updates.append(params)
                else:
                    self.select_params = params

            def fetchall(self):
                return batches.pop(0)

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                commits.append(1)

        class _Provider:
            def embed_documents(self, texts):
                return [[0.5] * 3 for _ in texts]

        done = reembed.reembed(_Conn(), _Provider(), "fastembed/x", 384, batch=2, log=lambda m: None)
        self.assertEqual(done, 3)
        self.assertEqual([u[3] for u in updates], [1, 2, 3])
        self.assertTrue(all(u[1] == "fastembed/x" and u[2] == 384 for u in updates))
        self.assertEqual(len(commits), 2)


if __name__ == "__main__":
    unittest.main()
