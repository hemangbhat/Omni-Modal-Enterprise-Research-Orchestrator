from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from omni_modal.config import BackendSettings, load_settings

ComponentState = Literal["ready", "contract", "deferred"]


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    state: ComponentState
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "state": self.state, "detail": self.detail}


class Phase1Orchestrator:
    def __init__(self, settings: BackendSettings | None = None) -> None:
        self._settings = settings or load_settings()

    def health(self) -> dict[str, object]:
        s = self._settings

        # Whisper: ready if WHISPER_MODEL_PATH is configured
        whisper_state: ComponentState = "ready" if s.whisper_model_path_configured else "deferred"
        whisper_detail = (
            f"LocalWhisperTranscriber is wired with model '{os.environ.get('WHISPER_MODEL_PATH', '')}'."
            if s.whisper_model_path_configured
            else "LocalWhisperTranscriber is available; set WHISPER_MODEL_PATH to activate."
        )

        # Entity extraction: ready if QLORA_ENTITY_MODEL_PATH is configured
        entity_state: ComponentState = "ready" if s.qlora_entity_model_path_configured else "ready"
        entity_detail = (
            f"HybridEntityExtractor wired with NER model '{os.environ.get('QLORA_ENTITY_MODEL_PATH', '')}'."
            if s.qlora_entity_model_path_configured
            else "RuleBasedEntityExtractor active (set QLORA_ENTITY_MODEL_PATH for HF NER model)."
        )

        # ADK: the DeterministicAgentGraph is the ADK-style workflow and is always active
        adk_state: ComponentState = "ready"
        adk_detail = (
            "DeterministicAgentGraph (ADK-style) is active: "
            "ValidateRequest → InternalRetrieval → MissingDataDetection → "
            "ExternalDelegation → EvidenceMerge → ReasoningSynthesis → ControlledFallback."
        )

        # External delegation
        a2a_state: ComponentState = "ready" if s.a2a_delegation_endpoint_configured else "deferred"
        a2a_detail = (
            "HttpA2AResearchClient is active."
            if s.a2a_delegation_endpoint_configured
            else "DisabledExternalResearchClient (set A2A_DELEGATION_ENDPOINT to enable)."
        )

        # Embedding
        embed_state: ComponentState = "ready"
        embed_detail = (
            f"Embedding backend: {s.embedding_backend} "
            f"(configured: {s.embedding_backend_configured})"
        )

        components = [
            ComponentStatus("configuration", "ready",
                            "Backend settings loaded with sensitive values redacted."),
            ComponentStatus("adk_orchestration", adk_state, adk_detail),
            ComponentStatus("internal_retrieval", "ready",
                            "PgVectorChunkRetriever or InMemoryChunkRetriever active."),
            ComponentStatus("embedding", embed_state, embed_detail),
            ComponentStatus("whisper_transcription", whisper_state, whisper_detail),
            ComponentStatus("entity_extraction", entity_state, entity_detail),
            ComponentStatus("external_delegation", a2a_state, a2a_detail),
            ComponentStatus("mcp_tool_server", "ready",
                            "McpToolServer with DocumentAccessGuard is wired."),
        ]

        return {
            "service": "omni-modal-api",
            "phase": 12,
            "status": "ok",
            "settings": self._settings.redacted(),
            "components": [component.as_dict() for component in components],
        }
