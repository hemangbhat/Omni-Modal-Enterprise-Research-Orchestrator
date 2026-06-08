from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from omni_modal.mcp.data_access import AuditSink, McpDataAccess
from omni_modal.mcp.models import (
    AuditLogSummary,
    ChunkSummary,
    DocumentDetail,
    DocumentSummary,
    EntitySummary,
    ToolContext,
)
from omni_modal.security.secrets import DATABASE_URL_SECRET, EnvSecretStore, SecretStore


@dataclass
class InMemoryMcpRepository(McpDataAccess, AuditSink):
    documents: list[DocumentDetail] = field(default_factory=list)
    chunks: list[ChunkSummary] = field(default_factory=list)
    entities: list[EntitySummary] = field(default_factory=list)
    audit_logs: list[AuditLogSummary] = field(default_factory=list)

    def search_documents(
        self,
        context: ToolContext,
        query: str,
        limit: int,
        status: str | None = None,
    ) -> list[DocumentSummary]:
        query_lower = query.lower()
        matches = [
            document
            for document in self.documents
            if query_lower in document.title.lower()
            and (status is None or document.status == status)
        ]
        return [
            DocumentSummary(
                id=document.id,
                title=document.title,
                source_type=document.source_type,
                status=document.status,
                owner_id=document.owner_id,
                updated_at=document.updated_at,
            )
            for document in matches[:limit]
        ]

    def get_document(
        self, context: ToolContext, document_id: str
    ) -> DocumentDetail | None:
        return next((document for document in self.documents if document.id == document_id), None)

    def search_chunks(
        self,
        context: ToolContext,
        query: str,
        limit: int,
        document_id: str | None = None,
    ) -> list[ChunkSummary]:
        terms = set(query.lower().split())
        ranked = []
        for chunk in self.chunks:
            if document_id is not None and chunk.document_id != document_id:
                continue
            score = sum(1 for term in terms if term in chunk.content.lower())
            if score > 0:
                ranked.append((score, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            ChunkSummary(**{**chunk.__dict__, "similarity": float(score)})
            for score, chunk in ranked[:limit]
        ]

    def get_entities(
        self,
        context: ToolContext,
        document_id: str,
        labels: list[str] | None = None,
        limit: int = 50,
    ) -> list[EntitySummary]:
        allowed = set(labels or [])
        return [
            entity
            for entity in self.entities
            if entity.document_id == document_id and (not allowed or entity.label in allowed)
        ][:limit]

    def get_audit_logs(
        self,
        context: ToolContext,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditLogSummary]:
        return [
            log
            for log in self.audit_logs
            if (resource_type is None or log.resource_type == resource_type)
            and (resource_id is None or log.resource_id == resource_id)
        ][:limit]

    def record_tool_call(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: dict[str, object],
        status: str,
    ) -> str:
        audit_id = str(uuid4())
        self.audit_logs.insert(
            0,
            AuditLogSummary(
                id=audit_id,
                actor_user_id=context.actor_user_id,
                action=f"mcp.{tool_name}",
                resource_type="mcp_tool",
                resource_id=None,
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                metadata={
                    "tenant_id": context.tenant_id,
                    "request_id": context.request_id or "",
                    "status": status,
                    "arguments": arguments,
                },
            ),
        )
        return audit_id


class PostgresMcpRepository(McpDataAccess, AuditSink):
    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self._secret_store = secret_store or EnvSecretStore()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import-not-found]
            from psycopg.rows import dict_row  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Postgres MCP tools require psycopg.") from exc

        return psycopg.connect(
            self._secret_store.get(DATABASE_URL_SECRET),
            row_factory=dict_row,
        )

    def search_documents(
        self,
        context: ToolContext,
        query: str,
        limit: int,
        status: str | None = None,
    ) -> list[DocumentSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select id, title, source_type, status, owner_id, updated_at
                from documents
                where tenant_id = %s
                  and title ilike %s
                  and (%s is null or status::text = %s)
                order by updated_at desc
                limit %s
                """,
                (context.tenant_id, f"%{query}%", status, status, limit),
            ).fetchall()
        return [_document_summary(row) for row in rows]

    def get_document(
        self, context: ToolContext, document_id: str
    ) -> DocumentDetail | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                select id, title, source_type, status, owner_id, source_uri, metadata, updated_at
                from documents
                where tenant_id = %s and id = %s
                """,
                (context.tenant_id, document_id),
            ).fetchone()
        return _document_detail(row) if row else None

    def search_chunks(
        self,
        context: ToolContext,
        query: str,
        limit: int,
        document_id: str | None = None,
    ) -> list[ChunkSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select c.id, c.document_id, d.title, c.chunk_index, c.content, c.metadata
                from document_chunks c
                inner join documents d on d.id = c.document_id
                where c.tenant_id = %s
                  and c.content ilike %s
                  and (%s is null or c.document_id = %s)
                order by c.chunk_index asc
                limit %s
                """,
                (context.tenant_id, f"%{query}%", document_id, document_id, limit),
            ).fetchall()
        return [_chunk_summary(row) for row in rows]

    def get_entities(
        self,
        context: ToolContext,
        document_id: str,
        labels: list[str] | None = None,
        limit: int = 50,
    ) -> list[EntitySummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select id, document_id, chunk_id, type, value, normalized_value, confidence
                from extracted_entities
                where tenant_id = %s
                  and document_id = %s
                  and (%s is null or type::text = any(%s))
                order by created_at desc
                limit %s
                """,
                (context.tenant_id, document_id, labels, labels, limit),
            ).fetchall()
        return [_entity_summary(row) for row in rows]

    def get_audit_logs(
        self,
        context: ToolContext,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditLogSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select id, actor_user_id, action, resource_type, resource_id, created_at, metadata
                from audit_logs
                where tenant_id = %s
                  and (%s is null or resource_type = %s)
                  and (%s is null or resource_id = %s)
                order by created_at desc
                limit %s
                """,
                (context.tenant_id, resource_type, resource_type, resource_id, resource_id, limit),
            ).fetchall()
        return [_audit_log_summary(row) for row in rows]

    def record_tool_call(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: dict[str, object],
        status: str,
    ) -> str:
        audit_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into audit_logs (
                  id, tenant_id, actor_user_id, action, resource_type, metadata
                )
                values (%s, %s, %s, %s, 'mcp_tool', %s)
                """,
                (
                    audit_id,
                    context.tenant_id,
                    context.actor_user_id,
                    f"mcp.{tool_name}",
                    {
                        "request_id": context.request_id,
                        "status": status,
                        "arguments": arguments,
                    },
                ),
            )
        return audit_id


def _document_summary(row: dict[str, Any]) -> DocumentSummary:
    return DocumentSummary(
        id=str(row["id"]),
        title=str(row["title"]),
        source_type=str(row["source_type"]),
        status=str(row["status"]),
        owner_id=str(row["owner_id"]),
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


def _document_detail(row: dict[str, Any]) -> DocumentDetail:
    return DocumentDetail(
        **_document_summary(row).__dict__,
        source_uri=str(row["source_uri"]) if row.get("source_uri") else None,
        metadata=dict(row.get("metadata") or {}),
    )


def _chunk_summary(row: dict[str, Any]) -> ChunkSummary:
    return ChunkSummary(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        title=str(row["title"]),
        chunk_index=int(row["chunk_index"]),
        content=str(row["content"]),
        metadata=dict(row.get("metadata") or {}),
    )


def _entity_summary(row: dict[str, Any]) -> EntitySummary:
    return EntitySummary(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        chunk_id=str(row["chunk_id"]) if row.get("chunk_id") else None,
        label=str(row["type"]),
        value=str(row["value"]),
        normalized_value=str(row["normalized_value"]) if row.get("normalized_value") else None,
        confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
    )


def _audit_log_summary(row: dict[str, Any]) -> AuditLogSummary:
    return AuditLogSummary(
        id=str(row["id"]),
        actor_user_id=str(row["actor_user_id"]) if row.get("actor_user_id") else None,
        action=str(row["action"]),
        resource_type=str(row["resource_type"]),
        resource_id=str(row["resource_id"]) if row.get("resource_id") else None,
        created_at=str(row["created_at"]),
        metadata=dict(row.get("metadata") or {}),
    )
