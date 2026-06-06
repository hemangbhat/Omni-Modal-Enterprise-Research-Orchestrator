from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omni_modal.data_access import EntityRecord


@dataclass(frozen=True)
class AgentRequest:
    tenant_id: str
    request_id: str
    user_id: str
    prompt: str


class Transcriber(Protocol):
    def transcribe(self, media_path: str) -> str:
        """Transcribe local audio or video into text."""


class EntityExtractor(Protocol):
    def extract(self, tenant_id: str, document_id: str, text: str) -> list[EntityRecord]:
        """Extract domain entities from text."""


class ExternalDelegationClient(Protocol):
    def delegate(self, request: AgentRequest) -> dict[str, object]:
        """Delegate work to an approved external agent endpoint."""


class Orchestrator(Protocol):
    def health(self) -> dict[str, object]:
        raise NotImplementedError
