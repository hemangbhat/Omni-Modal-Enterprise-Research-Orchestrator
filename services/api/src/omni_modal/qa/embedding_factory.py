"""Pluggable embedding-provider selection.

Selects an embedding backend from environment configuration while ALWAYS
preserving a working path: if a real backend is requested but unavailable
(missing key, missing library, or construction error), the factory falls back
to the deterministic hashing provider and records why via observability. This
keeps the local/offline demo working and never crashes startup.

Environment variables
----------------------
EMBEDDING_BACKEND          hashing | openai | sentence-transformers  (default: hashing)
EMBEDDING_DIMENSIONS       int, default 1536 (used by hashing + openai)
OPENAI_API_KEY             required when EMBEDDING_BACKEND=openai
OPENAI_EMBEDDING_MODEL     default text-embedding-3-small
OPENAI_BASE_URL            default https://api.openai.com/v1
SENTENCE_TRANSFORMERS_MODEL default sentence-transformers/all-MiniLM-L6-v2
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from omni_modal.observability import observability
from omni_modal.qa.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    HashingQueryEmbeddingProvider,
    OpenAIEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)

VALID_BACKENDS = ("hashing", "openai", "sentence-transformers")


@dataclass(frozen=True)
class EmbeddingSelection:
    """The provider that was actually constructed plus why."""

    provider: EmbeddingProvider
    backend: str  # the backend that is actually in use
    requested_backend: str  # what the env asked for
    fell_back: bool
    reason: str | None = None

    @property
    def is_semantic(self) -> bool:
        return self.backend in ("openai", "sentence-transformers")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def select_embedding_provider(
    *, env: dict[str, str] | None = None
) -> EmbeddingSelection:
    """Construct an embedding provider from environment configuration.

    Never raises: on any failure it returns the hashing fallback with
    ``fell_back=True`` and a human-readable ``reason``.
    """
    environ = env if env is not None else dict(os.environ)
    requested = (environ.get("EMBEDDING_BACKEND") or "hashing").strip().lower()
    dimensions = _int_env("EMBEDDING_DIMENSIONS", 1536)

    if requested not in VALID_BACKENDS:
        reason = f"Unknown EMBEDDING_BACKEND='{requested}'; using hashing fallback."
        observability.capture_message(
            reason, operation="embeddings.factory", level="warning"
        )
        return EmbeddingSelection(
            provider=HashingQueryEmbeddingProvider(dimensions=dimensions),
            backend="hashing",
            requested_backend=requested,
            fell_back=True,
            reason=reason,
        )

    if requested == "hashing":
        return EmbeddingSelection(
            provider=HashingQueryEmbeddingProvider(dimensions=dimensions),
            backend="hashing",
            requested_backend="hashing",
            fell_back=False,
        )

    if requested == "openai":
        api_key = environ.get("OPENAI_API_KEY", "")
        if not api_key:
            reason = "EMBEDDING_BACKEND=openai but OPENAI_API_KEY is not set; using hashing fallback."
            observability.capture_message(
                reason, operation="embeddings.factory", level="warning"
            )
            return _hashing_fallback("openai", dimensions, reason)
        try:
            provider = OpenAIEmbeddingProvider(
                api_key=api_key,
                model=environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                dimensions=dimensions,
                base_url=environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )
            return EmbeddingSelection(
                provider=provider,
                backend="openai",
                requested_backend="openai",
                fell_back=False,
            )
        except Exception as exc:  # construction is cheap; network happens lazily
            reason = f"Failed to initialise OpenAI embeddings: {exc}; using hashing fallback."
            observability.capture_message(
                reason, operation="embeddings.factory", level="error"
            )
            return _hashing_fallback("openai", dimensions, reason)

    # sentence-transformers
    try:
        provider = SentenceTransformerEmbeddingProvider(
            model_name=environ.get(
                "SENTENCE_TRANSFORMERS_MODEL",
                "sentence-transformers/all-MiniLM-L6-v2",
            )
        )
        return EmbeddingSelection(
            provider=provider,
            backend="sentence-transformers",
            requested_backend="sentence-transformers",
            fell_back=False,
        )
    except EmbeddingError as exc:
        reason = f"{exc}; using hashing fallback."
        observability.capture_message(
            reason, operation="embeddings.factory", level="error"
        )
        return _hashing_fallback("sentence-transformers", dimensions, reason)


def _hashing_fallback(
    requested: str, dimensions: int, reason: str
) -> EmbeddingSelection:
    return EmbeddingSelection(
        provider=HashingQueryEmbeddingProvider(dimensions=dimensions),
        backend="hashing",
        requested_backend=requested,
        fell_back=True,
        reason=reason,
    )
