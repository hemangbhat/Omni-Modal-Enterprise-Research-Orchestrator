from __future__ import annotations

from dataclasses import dataclass

from omni_modal.mcp.models import ToolContext, ToolName


class PermissionDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class ToolPermission:
    name: ToolName
    required_roles: frozenset[str]
    read_only: bool = True


TOOL_PERMISSIONS: dict[ToolName, ToolPermission] = {
    "search_documents": ToolPermission(
        "search_documents", frozenset({"researcher", "admin", "auditor"})
    ),
    "get_document": ToolPermission(
        "get_document", frozenset({"researcher", "admin", "auditor"})
    ),
    "search_chunks": ToolPermission(
        "search_chunks", frozenset({"researcher", "admin", "auditor"})
    ),
    "get_entities": ToolPermission(
        "get_entities", frozenset({"researcher", "admin", "auditor"})
    ),
    "get_audit_logs": ToolPermission(
        "get_audit_logs", frozenset({"admin", "auditor"})
    ),
}


class ToolPermissionGuard:
    def assert_allowed(self, context: ToolContext, tool_name: ToolName) -> None:
        permission = TOOL_PERMISSIONS[tool_name]
        roles = set(context.roles)
        if not roles.intersection(permission.required_roles):
            raise PermissionDeniedError(
                f"Tool {tool_name} requires one of: "
                f"{', '.join(sorted(permission.required_roles))}."
            )
