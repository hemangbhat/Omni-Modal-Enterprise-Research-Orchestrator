import io
import json
import unittest
from unittest import mock

import _path  # noqa: F401
from omni_modal.qa.embeddings import (
    EmbeddingError,
    HashingQueryEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from omni_modal.qa.embedding_factory import select_embedding_provider
from omni_modal.benchmark.embedding_compare import evaluate


# ---------------------------------------------------------------------------
# 1. Fallback path still works
# ---------------------------------------------------------------------------
class HashingFallbackTests(unittest.TestCase):
    def test_embed_query_is_deterministic_and_unit_norm(self) -> None:
        provider = HashingQueryEmbeddingProvider(dimensions=128)
        a = provider.embed_query("enterprise procurement compliance")
        b = provider.embed_query("enterprise procurement compliance")
        self.assertEqual(a, b)
        self.assertAlmostEqual(sum(v * v for v in a) ** 0.5, 1.0, places=6)

    def test_documents_batch_matches_singletons(self) -> None:
        provider = HashingQueryEmbeddingProvider(dimensions=64)
        texts = ["alpha beta", "gamma"]
        self.assertEqual(
            provider.embed_documents(texts),
            [provider.embed_document(texts[0]), provider.embed_document(texts[1])],
        )


# ---------------------------------------------------------------------------
# 2. Factory selection + honest fallback
# ---------------------------------------------------------------------------
class FactoryTests(unittest.TestCase):
    def test_default_is_hashing(self) -> None:
        selection = select_embedding_provider(env={})
        self.assertEqual(selection.backend, "hashing")
        self.assertFalse(selection.fell_back)
        self.assertFalse(selection.is_semantic)

    def test_unknown_backend_falls_back_to_hashing(self) -> None:
        selection = select_embedding_provider(env={"EMBEDDING_BACKEND": "magic"})
        self.assertEqual(selection.backend, "hashing")
        self.assertTrue(selection.fell_back)
        self.assertIn("Unknown", selection.reason or "")

    def test_openai_without_key_falls_back(self) -> None:
        selection = select_embedding_provider(env={"EMBEDDING_BACKEND": "openai"})
        self.assertEqual(selection.backend, "hashing")
        self.assertTrue(selection.fell_back)
        self.assertEqual(selection.requested_backend, "openai")

    def test_openai_with_key_selects_openai(self) -> None:
        selection = select_embedding_provider(
            env={"EMBEDDING_BACKEND": "openai", "OPENAI_API_KEY": "sk-test"}
        )
        self.assertEqual(selection.backend, "openai")
        self.assertFalse(selection.fell_back)
        self.assertTrue(selection.is_semantic)
        self.assertIsInstance(selection.provider, OpenAIEmbeddingProvider)

    def test_sentence_transformers_without_lib_falls_back(self) -> None:
        # sentence-transformers is an optional dependency; in CI it is absent,
        # so the factory must fall back to hashing rather than crash.
        selection = select_embedding_provider(
            env={"EMBEDDING_BACKEND": "sentence-transformers"}
        )
        # Either the lib is installed (real) or it falls back to hashing.
        self.assertIn(selection.backend, ("sentence-transformers", "hashing"))
        if selection.backend == "hashing":
            self.assertTrue(selection.fell_back)


# ---------------------------------------------------------------------------
# 3. Real OpenAI provider path works (HTTP mocked — no network, no key needed)
# ---------------------------------------------------------------------------
class OpenAIProviderTests(unittest.TestCase):
    def _fake_response(self, vectors: list[list[float]]):
        payload = {
            "data": [
                {"index": i, "embedding": vec} for i, vec in enumerate(vectors)
            ]
        }
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_embed_documents_batches_and_normalizes(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test", dimensions=3)
        raw = [[3.0, 0.0, 0.0], [0.0, 0.0, 4.0]]

        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = self._fake_response(raw)
            vectors = provider.embed_documents(["a", "b"])

        # L2-normalized
        self.assertAlmostEqual(vectors[0][0], 1.0, places=6)
        self.assertAlmostEqual(vectors[1][2], 1.0, places=6)
        self.assertAlmostEqual(sum(v * v for v in vectors[0]) ** 0.5, 1.0, places=6)

    def test_request_failure_raises_embedding_error(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test", dimensions=3)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            with self.assertRaises(EmbeddingError):
                provider.embed_query("hello")

    def test_empty_documents_returns_empty(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test", dimensions=3)
        self.assertEqual(provider.embed_documents([]), [])


# ---------------------------------------------------------------------------
# 4. Retrieval quality improves with semantic embeddings
#    (controlled, deterministic stub — proves the architecture benefits from
#     semantics; not a claim about any specific real model's numbers)
# ---------------------------------------------------------------------------
CONCEPTS: dict[str, set[str]] = {
    "cycle": {"procurement", "timelines", "deals", "close", "longer", "signing", "contracts", "buyers"},
    "retention": {"revenue", "retention", "customers", "growing", "accounts", "expanded", "spend", "existing"},
    "security": {"compliance", "access", "audit", "regulated", "build", "clients", "control", "logging"},
    "onboarding": {"onboarding", "signup", "free", "flow", "paused", "stop", "self", "serve"},
    "latency": {"query", "response", "latency", "faster", "search", "caching", "pooling", "second", "make"},
}
_CONCEPT_INDEX = {name: i for i, name in enumerate(CONCEPTS)}


class StubSemanticProvider:
    """Deterministic concept-based embedder: maps a passage to a one-hot vector
    over a small concept space by majority keyword vote. Paraphrases that share
    a concept (but not surface tokens) collapse to the same vector."""

    @property
    def dimensions(self) -> int:
        return len(CONCEPTS)

    def _concept_vector(self, text: str) -> list[float]:
        import re

        tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        best_name: str | None = None
        best_score = 0
        for name, keywords in CONCEPTS.items():
            score = len(tokens & keywords)
            if score > best_score:
                best_score = score
                best_name = name
        vector = [0.0] * len(CONCEPTS)
        if best_name is not None:
            vector[_CONCEPT_INDEX[best_name]] = 1.0
        return vector

    def embed_query(self, text: str) -> list[float]:
        return self._concept_vector(text)

    def embed_document(self, text: str) -> list[float]:
        return self._concept_vector(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._concept_vector(t) for t in texts]


class SemanticImprovementTests(unittest.TestCase):
    def test_semantic_outperforms_hashing_on_paraphrase_corpus(self) -> None:
        hashing = evaluate(HashingQueryEmbeddingProvider(), backend_label="hashing")
        semantic = evaluate(StubSemanticProvider(), backend_label="semantic-stub")

        # The semantic embedder resolves every paraphrase to its source doc.
        self.assertEqual(semantic.recall_at_1, 1.0)
        # Hashing cannot — the queries deliberately share few/no tokens.
        self.assertLess(hashing.recall_at_1, 1.0)
        # And semantics is at least as good on both metrics.
        self.assertGreaterEqual(semantic.recall_at_1, hashing.recall_at_1)
        self.assertGreaterEqual(semantic.mrr, hashing.mrr)


if __name__ == "__main__":
    unittest.main()
