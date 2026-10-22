"""Hugging Face embeddings that run inside the API, sized for a 512 MB server.

``BAAI/bge-small-en-v1.5`` in ONNX form (via ``fastembed``) gives 384-dim
vectors, the size of the ``embeddings.embedding`` column, so switching needs
no schema change. Measured on the evaluation corpus it is the most accurate
model tried (vector search recall@1 0.839, recall@5 0.982, MRR 0.899).

Memory: about 300 MB with one thread and small batches. The model loads on
first use, not at startup, so a health check never pays for it. If it cannot
load or run, callers get ``EmbeddingUnavailable`` and search falls back to
keyword ranking instead of failing.
"""

from __future__ import annotations

import os
import threading

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class EmbeddingUnavailable(RuntimeError):
    """The embedding model could not be loaded or run; use keyword search."""


class FastEmbedProvider:
    dimensions = 384

    def __init__(self, model_name: str | None = None, *, cache_dir: str | None = None,
                 threads: int = 1, batch_size: int = 4, loader=None) -> None:
        self.model_name = model_name or os.environ.get("FASTEMBED_MODEL", DEFAULT_MODEL)
        self.model_id = f"fastembed/{self.model_name}"
        self._cache_dir = cache_dir or os.environ.get("FASTEMBED_CACHE_DIR") or None
        self._threads = threads
        self._batch_size = batch_size
        self._loader = loader
        self._model = None
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def _get(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    if self._loader is not None:
                        self._model = self._loader(self.model_name)
                    else:
                        from fastembed import TextEmbedding  # noqa: PLC0415

                        self._model = TextEmbedding(self.model_name, cache_dir=self._cache_dir, threads=self._threads)
                except Exception as exc:  # noqa: BLE001 - missing package, download or memory
                    self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                    raise EmbeddingUnavailable(self.last_error) from exc
        return self._model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def embed_query(self, text: str) -> list[float]:
        model = self._get()
        try:
            # bge models expect a retrieval instruction on queries; query_embed adds it.
            vector = next(iter(model.query_embed([text])))
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            raise EmbeddingUnavailable(self.last_error) from exc
        return [float(x) for x in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._get()
        try:
            return [[float(x) for x in v] for v in model.embed(list(texts), batch_size=self._batch_size)]
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            raise EmbeddingUnavailable(self.last_error) from exc
