from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ToolName = Literal[
    "search_documents",
    "get_document",
    "search_chunks",
    "get_entities",
    "get_audit_logs",
]


@dataclass(frozen=True)
class ToolContext:
    tenant_id: str
    actor_user_id: str
    roles: tuple[str, ...] = ()
    request_id: str | None = None


@dataclass(frozen=True)
class DocumentSummary:
    id: str
    title: str
    source_type: str
    status: str
    owner_id: str
    updated_at: str | None = None


@dataclass(frozen=True)
class DocumentDetail(DocumentSummary):
    source_uri: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkSummary:
    id: str
    document_id: str
    title: str
    chunk_index: int
    content: str
    similarity: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntitySummary:
    id: str
    document_id: str
    chunk_id: str | None
    label: str
    value: str
    normalized_value: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class AuditLogSummary:
    id: str
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    tool: ToolName
    status: Literal["ok", "denied", "error"]
    data: dict[str, Any]
    audit_id: str | None = None
    error: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolDefinition:
    name: ToolName
    description: str
    input_schema: dict[str, Any]

    def to_mcp_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
