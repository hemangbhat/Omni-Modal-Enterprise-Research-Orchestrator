import unittest

import _path  # noqa: F401
from omni_modal.ingestion.models import (
    IngestionResult,
    SourceReference,
    StructuredChunk,
)
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.in_memory_store import (
    InMemoryChunkPersistence,
    InMemoryChunkRetriever,
    InMemoryVectorStore,
    cosine_similarity,
)
from omni_modal.qa.models import QueryRequest


def _result(tenant: str, document_id: str, paragraphs: list[str], status: str = "ready") -> IngestionResult:
    ref = SourceReference(source_path=f"mem://{document_id}", source_kind="pdf")
    chunks = [
        StructuredChunk(
            chunk_index=i,
            content=text,
            content_hash=f"{document_id}-{i}",
            source=ref,
            start_word=0,
            end_word=len(text.split()),
        )
        for i, text in enumerate(paragraphs)
    ]
    return IngestionResult(
        tenant_id=tenant,
        document_id=document_id,
        owner_id="owner",
        title=f"Doc {document_id}",
        source_kind="pdf",
        status=status,  # type: ignore[arg-type]
        chunks=chunks,
        metadata={},
    )


class CosineSimilarityTests(unittest.TestCase):
    def test_identical_vectors_similarity_one(self) -> None:
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=6)

    def test_zero_vector_returns_zero(self) -> None:
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_dimension_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            cosine_similarity([1.0], [1.0, 2.0])


class PersistenceAndRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = HashingQueryEmbeddingProvider(dimensions=256)
        self.store = InMemoryVectorStore()
        self.persistence = InMemoryChunkPersistence(self.store, self.provider)
        self.retriever = InMemoryChunkRetriever(self.provider, self.store)

    def test_persist_then_retrieve_returns_relevant_chunk_first(self) -> None:
        count = self.persistence.persist(
            _result(
                "t1",
                "doc1",
                [
                    "Enterprise procurement cycles lengthened due to compliance review.",
                    "The cafeteria now serves oat milk lattes on Fridays.",
                ],
            )
        )
        self.assertEqual(count, 2)
        self.assertEqual(len(self.store), 2)

        results = self.retriever.retrieve(
            QueryRequest(
                tenant_id="t1",
                user_id="u1",
                question="Why did enterprise procurement cycles lengthen?",
                top_k=2,
            )
        )
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("procurement", results[0].content.lower())

    def test_retrieval_is_tenant_scoped(self) -> None:
        self.persistence.persist(_result("tenant-a", "docA", ["alpha content here"]))
        self.persistence.persist(_result("tenant-b", "docB", ["alpha content here"]))

        results = self.retriever.retrieve(
            QueryRequest(tenant_id="tenant-a", user_id="u", question="alpha", top_k=10)
        )
        self.assertTrue(results)
        self.assertTrue(all(r.document_id == "docA" for r in results))

    def test_top_k_limits_results(self) -> None:
        self.persistence.persist(
            _result("t", "d", [f"chunk number {i} alpha" for i in range(10)])
        )
        results = self.retriever.retrieve(
            QueryRequest(tenant_id="t", user_id="u", question="alpha", top_k=3)
        )
        self.assertEqual(len(results), 3)

    def test_failed_result_not_persisted(self) -> None:
        count = self.persistence.persist(
            _result("t", "d", ["text"], status="failed")
        )
        self.assertEqual(count, 0)
        self.assertEqual(len(self.store), 0)

    def test_persist_is_idempotent_on_reingest(self) -> None:
        self.persistence.persist(_result("t", "d", ["first version of the text"]))
        self.persistence.persist(_result("t", "d", ["second version of the text"]))
        # Same chunk_id (d-0) → replaced, not duplicated.
        self.assertEqual(len(self.store), 1)


if __name__ == "__main__":
    unittest.main()
