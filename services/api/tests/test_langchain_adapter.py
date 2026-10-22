"""LangChain adapters: OMERO as a LangChain retriever/tool, and LangChain parts inside OMERO."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from omni_modal.benchmark.eval import evaluate, langchain_available, load_corpus

if langchain_available():
    from omni_modal.integrations.langchain_adapter import (
        LangChainHybridRetriever,
        OmeroRetriever,
        omero_search_tool,
    )
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.in_memory_store import InMemoryChunkRetriever, InMemoryVectorStore, StoredChunk
from omni_modal.qa.models import QueryRequest


def _store(provider) -> InMemoryVectorStore:
    store = InMemoryVectorStore()
    rows = [
        ("t1", "a", "Contract", "The Nimbus hosting contract renews on 1 March 2027."),
        ("t1", "b", "Budget", "The FY2027 budget is 31.5 million dollars."),
        ("t2", "x", "Secret", "Tenant two's Nimbus contract is private."),
    ]
    for tenant, doc, title, text in rows:
        store.add(StoredChunk(tenant, doc, title, "pdf", f"{doc}:0", 0, text, provider.embed_query(text),
                              {"page_number": 4}))
    return store


@unittest.skipUnless(langchain_available(), "install the [langchain] extra")
class LangChainAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = HashingQueryEmbeddingProvider()
        self.store = _store(self.provider)

    def test_omero_retriever_is_fixed_to_its_tenant_and_scope(self) -> None:
        base = InMemoryChunkRetriever(self.provider, self.store)
        docs = OmeroRetriever(retriever=base, tenant_id="t1", user_id="u", top_k=5).invoke("Nimbus contract")
        self.assertEqual({d.metadata["document_id"] for d in docs}, {"a", "b"})
        self.assertEqual(docs[0].metadata["title"], "Contract")
        self.assertEqual(docs[0].metadata["page_number"], 4)
        scoped = OmeroRetriever(retriever=base, tenant_id="t1", user_id="u", document_ids=("b",)).invoke("Nimbus")
        self.assertEqual([d.metadata["document_id"] for d in scoped], ["b"])

    def test_search_tool_returns_numbered_passages_marked_untrusted(self) -> None:
        base = InMemoryChunkRetriever(self.provider, self.store)
        tool = omero_search_tool(OmeroRetriever(retriever=base, tenant_id="t1", user_id="u", top_k=1))
        self.assertEqual(tool.name, "search_workspace_documents")
        out = tool.invoke({"query": "When does the Nimbus contract renew?"})
        self.assertIn("untrusted", out)
        self.assertIn("[1] Contract, page 4: The Nimbus hosting contract renews", out)
        self.assertNotIn("Tenant two", out)

    def test_langchain_retriever_keeps_isolation_scope_and_searchable(self) -> None:
        for mode in ("vector", "hybrid"):
            r = LangChainHybridRetriever(self.provider, self.store, mode=mode)
            out = r.retrieve(QueryRequest("t1", "u", "Nimbus contract", top_k=5))
            self.assertEqual({c.document_id for c in out}, {"a", "b"}, mode)
            self.assertEqual(out[0].document_id, "a", mode)
            r.is_searchable = lambda tenant, doc: doc != "a"
            self.assertEqual([c.document_id for c in r.retrieve(QueryRequest("t1", "u", "Nimbus", top_k=5))], ["b"])
            self.assertEqual(r.retrieve(QueryRequest("t1", "u", "Nimbus", document_ids=())), [])
        with self.assertRaises(ValueError):
            LangChainHybridRetriever(self.provider, self.store, mode="magic")

    def test_langchain_vector_matches_omero_vector_on_the_corpus(self) -> None:
        corpus = load_corpus()
        provider = HashingQueryEmbeddingProvider()
        omero, _ = evaluate(corpus, "hashing", "vector", provider)
        lc, _ = evaluate(corpus, "hashing", "langchain-vector", provider)
        # Same embeddings, same data: the adapter must not change what vector search finds.
        self.assertEqual((lc.recall_at_1, lc.recall_at_5), (omero.recall_at_1, omero.recall_at_5))
        self.assertAlmostEqual(lc.mrr, omero.mrr, delta=0.01)


if __name__ == "__main__":
    unittest.main()
