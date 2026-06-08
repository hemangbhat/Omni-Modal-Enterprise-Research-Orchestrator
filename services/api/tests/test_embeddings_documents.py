import unittest

import _path  # noqa: F401
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider


class DocumentEmbeddingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = HashingQueryEmbeddingProvider(dimensions=128)

    def test_embed_document_matches_embed_query(self) -> None:
        text = "enterprise procurement compliance"
        self.assertEqual(
            self.provider.embed_document(text), self.provider.embed_query(text)
        )

    def test_embed_documents_preserves_order_and_count(self) -> None:
        texts = ["alpha beta", "gamma delta", "epsilon"]
        vectors = self.provider.embed_documents(texts)
        self.assertEqual(len(vectors), 3)
        for text, vector in zip(texts, vectors):
            self.assertEqual(vector, self.provider.embed_query(text))

    def test_embed_documents_empty_list(self) -> None:
        self.assertEqual(self.provider.embed_documents([]), [])

    def test_dimensions_property(self) -> None:
        self.assertEqual(self.provider.dimensions, 128)

    def test_vectors_are_unit_length_when_nonzero(self) -> None:
        vector = self.provider.embed_document("alpha beta gamma")
        magnitude = sum(v * v for v in vector) ** 0.5
        self.assertAlmostEqual(magnitude, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
