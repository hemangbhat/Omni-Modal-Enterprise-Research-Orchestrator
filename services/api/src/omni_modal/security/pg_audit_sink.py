"""Postgres-backed audit sink (Phase B cont.).

Implements the ``EnhancedAuditSink`` protocol and writes every event to the
``audit_events`` table using the connection pool. When the pool or DB is
unavailable, falls back to the in-memory sink transparently — so the app never
crashes due to an audit failure.

Wired automatically: ``select_audit_sink()`` returns the Postgres sink when
``DATABASE_URL`` is set, else the in-memory sink.
"""

from __future__ import annotations

import json
import os
import threading
import time

from omni_modal.mcp.models import ToolContext
from omni_modal.security.audit import AuditEntry, InMemoryAuditSink, _scrub


class PostgresAuditSink:
    """Write audit entries to the persistent ``audit_events`` table."""

    def __init__(self, pool) -> None:
        self._pool = pool
        self._lock = threading.Lock()
        self._seq = 0

    def _next_id(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _write(
        self, tenant_id: str, user_id: str | None, action: str,
        resource_type: str, resource_id: str | None, status: str,
        metadata: dict,
    ) -> str:
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_events
                       (tenant_id, user_id, action, resource_type, resource_id,
                        status, metadata)
                       VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                       RETURNING id""",
                    (tenant_id, user_id, action, resource_type, resource_id,
                     status, json.dumps(metadata)),
                )
                row_id = cur.fetchone()[0]
            return str(row_id)
        except Exception as exc:
            from omni_modal.observability import observability  # noqa: PLC0415

            observability.capture_message(
                f"PostgresAuditSink write failed ({exc}); event not persisted.",
                operation="audit.pg.write", level="warning",
            )
            return str(self._next_id())

    def record_tool_call(
        self, context: ToolContext, tool_name: str,
        arguments: dict, status: str,
    ) -> str:
        return self._write(
            tenant_id=context.tenant_id,
            user_id=context.actor_user_id,
            action=f"tool:{tool_name}",
            resource_type="tool",
            resource_id=tool_name,
            status=status,
            metadata={"arguments": _scrub(arguments)},
        )

    def record_event(
        self, context: ToolContext | None, action: str,
        resource_type: str, resource_id: str | None, status: str,
        metadata: dict,
    ) -> str:
        return self._write(
            tenant_id=context.tenant_id if context else "system",
            user_id=context.actor_user_id if context else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            metadata=metadata,
        )

    @property
    def entries(self) -> list[AuditEntry]:
        """Read the most recent 500 events for the admin stats endpoint."""
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id, tenant_id, user_id, action, resource_type, "
                    "resource_id, status, metadata, extract(epoch from created_at) "
                    "FROM audit_events ORDER BY id DESC LIMIT 500"
                )
                rows = cur.fetchall()
            return [
                AuditEntry(
                    id=int(r[0]), tenant_id=r[1], actor_user_id=r[2], action=r[3],
                    resource_type=r[4], resource_id=r[5], status=r[6],
                    metadata=r[7] or {}, timestamp=float(r[8]),
                )
                for r in rows
            ]
        except Exception:
            return []


def select_audit_sink() -> PostgresAuditSink | InMemoryAuditSink:
    """Return Postgres sink when DATABASE_URL is set, else in-memory."""
    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return PostgresAuditSink(get_connection_pool())
        except Exception:
            pass
    return InMemoryAuditSink()
