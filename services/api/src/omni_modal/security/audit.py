from __future__ import annotations
import itertools
import time
from dataclasses import dataclass, field
from typing import Protocol

from omni_modal.mcp.models import ToolContext


@dataclass
class AuditEntry:
    id: int
    tenant_id: str
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    status: str
    metadata: dict[str, object]
    timestamp: float  # monotonic time (seconds)


class EnhancedAuditSink(Protocol):
    def record_tool_call(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: dict[str, object],
        status: str,
    ) -> str:
        ...

    def record_event(
        self,
        context: ToolContext | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        status: str,
        metadata: dict[str, object],
    ) -> str:
        ...


class InMemoryAuditSink:
    """Thread-unsafe in-memory audit sink for testing."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._entries: list[AuditEntry] = []

    def record_tool_call(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: dict[str, object],
        status: str,
    ) -> str:
        entry = AuditEntry(
            id=next(self._counter),
            tenant_id=context.tenant_id,
            actor_user_id=context.actor_user_id,
            action=f"tool:{tool_name}",
            resource_type="tool",
            resource_id=tool_name,
            status=status,
            metadata={"arguments": _scrub(arguments)},
            timestamp=time.monotonic(),
        )
        self._entries.append(entry)
        return str(entry.id)

    def record_event(
        self,
        context: ToolContext | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        status: str,
        metadata: dict[str, object],
    ) -> str:
        entry = AuditEntry(
            id=next(self._counter),
            tenant_id=context.tenant_id if context else "system",
            actor_user_id=context.actor_user_id if context else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            metadata=metadata,
            timestamp=time.monotonic(),
        )
        self._entries.append(entry)
        return str(entry.id)

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)


def _scrub(arguments: dict[str, object]) -> dict[str, object]:
    """Replace non-primitive values with <scrubbed>."""
    result: dict[str, object] = {}
    for key, value in arguments.items():
        if isinstance(value, (int, float, bool)) or value is None:
            result[key] = value
        else:
            result[key] = "<scrubbed>"
    return result
