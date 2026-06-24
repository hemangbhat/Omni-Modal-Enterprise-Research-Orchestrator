from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request
from typing import Protocol, runtime_checkable


@runtime_checkable
class QueryEmbeddingProvider(Protocol):
    """Minimal contract required by the retrieval layer.

    Any provider used for retrieval MUST implement ``embed_query``. Providers
    that also persist documents implement ``embed_document`` /
    ``embed_documents`` and expose ``dimensions`` (see ``EmbeddingProvider``).
    """

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


@runtime_checkable
class EmbeddingProvider(QueryEmbeddingProvider, Protocol):
    """Full embedding contract used for both ingestion and retrieval."""

    @property
    def dimensions(self) -> int:
        ...

    def embed_document(self, text: str) -> list[float]:
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...


class EmbeddingError(RuntimeError):
    """Raised when a real embedding backend fails to produce vectors."""


class HashingQueryEmbeddingProvider:
    """Deterministic local embedding fallback for internal-only development.

    NOTE: This is a bag-of-words hashing embedder. It is deterministic and
    useful for wiring/tests, but it is NOT semantically meaningful. Two
    paraphrases with no shared tokens will not be considered similar. For real
    relevance, inject a production embedding provider (e.g., an API-backed one).
    """

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def __init__(self, dimensions: int = 1536) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be greater than zero.")
        self._dimensions = dimensions

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            return vector
        return [value / magnitude for value in vector]

    def embed_document(self, text: str) -> list[float]:
        """Embed a single document chunk.

        Uses the same deterministic hashing scheme as ``embed_query`` so that
        a query and a chunk containing the same tokens produce aligned vectors.
        """
        return self.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch-embed document chunks. Order is preserved 1:1 with ``texts``."""
        return [self.embed_query(text) for text in texts]


def _l2_normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]


class OpenAIEmbeddingProvider:
    """Real semantic embeddings via the OpenAI embeddings REST API.

    Uses only the Python standard library (``urllib``) so it adds no hard
    dependency. Vectors are L2-normalized so cosine similarity and dot product
    agree, matching the in-memory and pgvector retrieval math.

    Default model ``text-embedding-3-small`` returns 1536 dims, which matches
    the ``embeddings.embedding vector(1536)`` column, so this backend is
    compatible with BOTH the in-memory and pgvector retrieval paths.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        max_batch: int = 256,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAIEmbeddingProvider requires an API key.")
        if dimensions < 1:
            raise ValueError("dimensions must be greater than zero.")
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_batch = max(1, max_batch)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _request(self, inputs: list[str]) -> list[list[float]]:
        body: dict[str, object] = {"model": self._model, "input": inputs}
        # text-embedding-3-* support reducing output dimensionality.
        if self._model.startswith("text-embedding-3"):
            body["dimensions"] = self._dimensions
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # urllib raises a variety of errors
            raise EmbeddingError(f"OpenAI embeddings request failed: {exc}") from exc

        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(inputs):
            raise EmbeddingError("OpenAI embeddings response shape was unexpected.")
        ordered = sorted(items, key=lambda item: int(item.get("index", 0)))
        return [_l2_normalize([float(v) for v in item["embedding"]]) for item in ordered]

    def embed_query(self, text: str) -> list[float]:
        return self._request([text])[0]

    def embed_document(self, text: str) -> list[float]:
        return self._request([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self._max_batch):
            out.extend(self._request(texts[start : start + self._max_batch]))
        return out


class SentenceTransformerEmbeddingProvider:
    """Real local semantic embeddings via the ``sentence-transformers`` library.

    Runs fully offline (after the model is downloaded once) — no API key
    required. The default model ``all-MiniLM-L6-v2`` produces 384-dim vectors,
    which work with the in-memory retrieval path. NOTE: the pgvector schema
    column is ``vector(1536)``; to use this backend with pgvector you must use a
    1536-dim model or alter the column. ``dimensions`` reflects the loaded model.

    The ``sentence-transformers`` package is an OPTIONAL dependency. If it is
    not installed, constructing this provider raises ``EmbeddingError``.

    Asymmetric retrieval instructions: some strong small models (BGE, E5)
    expect an instruction prefix on the *query* side only. This provider
    applies the correct prefix automatically based on the model name so
    retrieval quality is not silently degraded.
    """

    def __init__(self, *, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # ImportError or runtime import failure
            raise EmbeddingError(
                "sentence-transformers is not installed. Install it with "
                "`pip install sentence-transformers` to use this backend."
            ) from exc
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._dimensions = int(self._model.get_sentence_embedding_dimension())
        self._query_prefix, self._doc_prefix = self._prefixes_for(model_name)

    @staticmethod
    def _prefixes_for(model_name: str) -> tuple[str, str]:
        """Return (query_prefix, doc_prefix) for the model family.

        - BGE v1.5 English: query instruction, no passage prefix.
        - E5 family: ``query:`` / ``passage:`` prefixes.
        - Everything else (e.g. MiniLM): no prefixes (symmetric).
        """
        name = model_name.lower()
        if "bge" in name and "en" in name:
            return ("Represent this sentence for searching relevant passages: ", "")
        if name.startswith("intfloat/e5") or "/e5-" in name or name.startswith("e5-"):
            return ("query: ", "passage: ")
        return ("", "")

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(
            [self._query_prefix + text], normalize_embeddings=True
        )[0]
        return [float(v) for v in vector]

    def embed_document(self, text: str) -> list[float]:
        vector = self._model.encode(
            [self._doc_prefix + text], normalize_embeddings=True
        )[0]
        return [float(v) for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        prefixed = [self._doc_prefix + t for t in texts]
        vectors = self._model.encode(prefixed, normalize_embeddings=True)
        return [[float(v) for v in row] for row in vectors]
