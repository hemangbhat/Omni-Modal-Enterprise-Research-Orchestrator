from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    tenant_id: str
    title: str
    body_text: str | None
    source_type: str
    source_uri: str | None


@dataclass(frozen=True)
class EntityRecord:
    id: str
    document_id: str
    tenant_id: str
    label: str
    value: str
    confidence: float | None


@dataclass(frozen=True)
class VectorSearchResult:
    document: DocumentRecord
    score: float


class ResearchDataAccess(Protocol):
    """Credential-free interface exposed to orchestration and agents."""

    def get_document(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        raise NotImplementedError

    def save_entities(
        self, tenant_id: str, document_id: str, entities: list[EntityRecord]
    ) -> None:
        raise NotImplementedError

    def search_similar(
        self, tenant_id: str, embedding: list[float], limit: int
    ) -> list[VectorSearchResult]:
        raise NotImplementedError
