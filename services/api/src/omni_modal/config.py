from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendSettings:
    environment: str
    database_url_configured: bool
    sentry_dsn_configured: bool
    whisper_model_path_configured: bool
    qlora_entity_model_path_configured: bool
    adk_project_id_configured: bool
    a2a_delegation_endpoint_configured: bool
    gemini_interactions_endpoint_configured: bool
    embedding_backend: str
    embedding_backend_configured: bool

    def redacted(self) -> dict[str, str | bool]:
        return {
            "environment": self.environment,
            "database_url_configured": self.database_url_configured,
            "sentry_dsn_configured": self.sentry_dsn_configured,
            "whisper_model_path_configured": self.whisper_model_path_configured,
            "qlora_entity_model_path_configured": self.qlora_entity_model_path_configured,
            "adk_project_id_configured": self.adk_project_id_configured,
            "a2a_delegation_endpoint_configured": self.a2a_delegation_endpoint_configured,
            "gemini_interactions_endpoint_configured": self.gemini_interactions_endpoint_configured,
            "embedding_backend": self.embedding_backend,
            "embedding_backend_configured": self.embedding_backend_configured,
        }


def _embedding_backend_configured(backend: str) -> bool:
    """True when the selected real backend has the config it needs."""
    if backend == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    if backend == "sentence-transformers":
        return True  # no key required; availability is checked at runtime
    return backend == "hashing"


def load_settings() -> BackendSettings:
    embedding_backend = (
        os.environ.get("EMBEDDING_BACKEND", "hashing").strip().lower() or "hashing"
    )
    return BackendSettings(
        environment=os.environ.get("ENVIRONMENT", "development"),
        database_url_configured=bool(os.environ.get("DATABASE_URL")),
        sentry_dsn_configured=bool(os.environ.get("SENTRY_DSN")),
        whisper_model_path_configured=bool(os.environ.get("WHISPER_MODEL_PATH")),
        qlora_entity_model_path_configured=bool(
            os.environ.get("QLORA_ENTITY_MODEL_PATH")
        ),
        adk_project_id_configured=bool(os.environ.get("ADK_PROJECT_ID")),
        a2a_delegation_endpoint_configured=bool(
            os.environ.get("A2A_DELEGATION_ENDPOINT")
        ),
        gemini_interactions_endpoint_configured=bool(
            os.environ.get("GEMINI_INTERACTIONS_ENDPOINT")
        ),
        embedding_backend=embedding_backend,
        embedding_backend_configured=_embedding_backend_configured(embedding_backend),
    )
