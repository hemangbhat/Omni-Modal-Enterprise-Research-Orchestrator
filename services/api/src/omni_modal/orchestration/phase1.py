from __future__ import annotations

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
        components = [
            ComponentStatus(
                "configuration",
                "ready",
                "Backend settings are loaded with sensitive values redacted.",
            ),
            ComponentStatus(
                "internal_data_access",
                "contract",
                "ResearchDataAccess protocol exists; MCP-backed implementation is deferred.",
            ),
            ComponentStatus(
                "whisper_transcription",
                "deferred",
                "Transcriber protocol exists; local Whisper runtime is deferred.",
            ),
            ComponentStatus(
                "qlora_entity_extraction",
                "deferred",
                "EntityExtractor protocol exists; QLoRA model loading is deferred.",
            ),
            ComponentStatus(
                "external_delegation",
                "deferred",
                "ExternalDelegationClient protocol exists; ADK, A2A, and Gemini calls are deferred.",
            ),
        ]

        return {
            "service": "omni-modal-api",
            "phase": 1,
            "status": "ok",
            "settings": self._settings.redacted(),
            "components": [component.as_dict() for component in components],
        }
