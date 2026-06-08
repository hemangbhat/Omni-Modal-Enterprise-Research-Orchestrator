from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

from omni_modal.mcp.data_access import AuditSink, McpDataAccess
from omni_modal.mcp.models import (
    AuditLogSummary, ChunkSummary, DocumentDetail,
    DocumentSummary, EntitySummary, ToolContext,
)


Visibility = Literal["private", "tenant", "restricted"]


@dataclass
class AccessMetadata:
    visibility: Visibility = "tenant"
    owner_id: str | None = None
    allowed_user_ids: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)


class AccessDenied(Exception):
    pass


def check_access(
    context: ToolContext,
    doc_tenant_id: str,
    meta: AccessMetadata,
) -> bool:
    """Return True iff the caller may read this document."""
    if context.tenant_id != doc_tenant_id:
        return False
    if meta.visibility == "private":
        return context.actor_user_id == meta.owner_id
    if meta.visibility == "tenant":
        return True
    if meta.visibility == "restricted":
        user_ok = context.actor_user_id in meta.allowed_user_ids
        role_ok = bool(frozenset(context.roles) & frozenset(meta.allowed_roles))
        return user_ok or role_ok
    return False


class DocumentAccessGuard:
    """Wraps McpDataAccess and filters results by AccessMetadata."""

    def __init__(
        self, inner: McpDataAccess, audit_sink: AuditSink
    ) -> None:
        self._inner = inner
        self._audit = audit_sink

    def search_documents(
        self, context: ToolContext, query: str, limit: int, status: str | None = None
    ) -> list[DocumentSummary]:
        results = self._inner.search_documents(context, query, limit, status)
        return [d for d in results if self._allowed_summary(context, d)]

    def get_document(
        self, context: ToolContext, document_id: str
    ) -> DocumentDetail | None:
        doc = self._inner.get_document(context, document_id)
        if doc is None:
            return None
        if not self._allowed_detail(context, doc):
            return None  # return empty rather than 404 to avoid oracle attacks
        return doc

    def search_chunks(
        self, context: ToolContext, query: str, limit: int,
        document_id: str | None = None,
    ) -> list[ChunkSummary]:
        chunks = self._inner.search_chunks(context, query, limit, document_id)
        return [c for c in chunks if self._chunk_allowed(context, c)]

    def get_entities(
        self, context: ToolContext, document_id: str,
        labels: list[str] | None = None, limit: int = 50,
    ) -> list[EntitySummary]:
        entities = self._inner.get_entities(context, document_id, labels, limit)
        return [e for e in entities if self._entity_allowed(context, e)]

    def get_audit_logs(
        self, context: ToolContext, resource_type: str | None = None,
        resource_id: str | None = None, limit: int = 50,
    ) -> list[AuditLogSummary]:
        # Audit logs are tenant-scoped by the data layer; no additional filter needed.
        return self._inner.get_audit_logs(context, resource_type, resource_id, limit)

    # --- private helpers ---

    def _allowed_summary(self, context: ToolContext, doc: DocumentSummary) -> bool:
        meta = AccessMetadata(visibility="tenant", owner_id=doc.owner_id)
        return check_access(context, context.tenant_id, meta)

    def _allowed_detail(self, context: ToolContext, doc: DocumentDetail) -> bool:
        visibility: Visibility = doc.metadata.get("visibility", "tenant")  # type: ignore[assignment]
        meta = AccessMetadata(
            visibility=visibility,
            owner_id=doc.owner_id,
            allowed_user_ids=doc.metadata.get("allowed_user_ids", []),
            allowed_roles=doc.metadata.get("allowed_roles", []),
        )
        return check_access(context, context.tenant_id, meta)

    def _chunk_allowed(self, context: ToolContext, chunk: ChunkSummary) -> bool:
        visibility: Visibility = chunk.metadata.get("visibility", "tenant")  # type: ignore[assignment]
        meta = AccessMetadata(
            visibility=visibility,
            owner_id=chunk.metadata.get("owner_id"),
            allowed_user_ids=chunk.metadata.get("allowed_user_ids", []),
            allowed_roles=chunk.metadata.get("allowed_roles", []),
        )
        return check_access(context, context.tenant_id, meta)

    def _entity_allowed(self, context: ToolContext, entity: EntitySummary) -> bool:
        # Entity access is gated at the document level; no separate entity metadata.
        return True
