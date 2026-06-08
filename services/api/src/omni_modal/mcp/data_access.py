from __future__ import annotations

from typing import Protocol

from omni_modal.mcp.models import (
    AuditLogSummary,
    ChunkSummary,
    DocumentDetail,
    DocumentSummary,
    EntitySummary,
    ToolContext,
)


class McpDataAccess(Protocol):
    """Credential-free data boundary used by MCP tools."""

    def search_documents(
        self,
        context: ToolContext,
        query: str,
        limit: int,
        status: str | None = None,
    ) -> list[DocumentSummary]:
        raise NotImplementedError

    def get_document(
        self,
        context: ToolContext,
        document_id: str,
    ) -> DocumentDetail | None:
        raise NotImplementedError

    def search_chunks(
        self,
        context: ToolContext,
        query: str,
        limit: int,
        document_id: str | None = None,
    ) -> list[ChunkSummary]:
        raise NotImplementedError

    def get_entities(
        self,
        context: ToolContext,
        document_id: str,
        labels: list[str] | None = None,
        limit: int = 50,
    ) -> list[EntitySummary]:
        raise NotImplementedError

    def get_audit_logs(
        self,
        context: ToolContext,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditLogSummary]:
        raise NotImplementedError


class AuditSink(Protocol):
    def record_tool_call(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: dict[str, object],
        status: str,
    ) -> str:
        raise NotImplementedError
