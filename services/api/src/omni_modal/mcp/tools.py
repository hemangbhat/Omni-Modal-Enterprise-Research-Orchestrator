from __future__ import annotations

import time
from typing import Any, Callable

from omni_modal.mcp.data_access import AuditSink, McpDataAccess
from omni_modal.mcp.models import ToolContext, ToolDefinition, ToolName, ToolResult
from omni_modal.mcp.permissions import PermissionDeniedError, ToolPermissionGuard
from omni_modal.observability import observability, scrub_pii
from omni_modal.retry import truncate


TOOL_DEFINITIONS: dict[ToolName, ToolDefinition] = {
    "search_documents": ToolDefinition(
        name="search_documents",
        description="Search tenant-scoped internal documents by title and metadata.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "status": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "get_document": ToolDefinition(
        name="get_document",
        description="Get one tenant-scoped internal document metadata record.",
        input_schema={
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
    ),
    "search_chunks": ToolDefinition(
        name="search_chunks",
        description="Search tenant-scoped document chunks for internal evidence.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "document_id": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "get_entities": ToolDefinition(
        name="get_entities",
        description="Get extracted enterprise entities for a tenant-scoped document.",
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["document_id"],
            "additionalProperties": False,
        },
    ),
    "get_audit_logs": ToolDefinition(
        name="get_audit_logs",
        description="Get tenant-scoped tool audit logs. Requires admin or auditor role.",
        input_schema={
            "type": "object",
            "properties": {
                "resource_type": {"type": "string"},
                "resource_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    ),
}


class ToolValidationError(ValueError):
    pass


class McpToolRouter:
    # Task 11.7 — DocumentAccessGuard integration:
    # When constructing McpToolRouter, wrap the `data_access` argument with
    # DocumentAccessGuard to enforce per-document access control, e.g.:
    #
    #   from omni_modal.security import DocumentAccessGuard
    #   guarded = DocumentAccessGuard(inner=raw_data_access, audit_sink=audit_sink)
    #   router = McpToolRouter(data_access=guarded, audit_sink=audit_sink)
    #
    # This is done at the construction site (mcp/cli.py or mcp/server.py) rather
    # than here so that the guard receives the same audit_sink as the router.
    def __init__(
        self,
        data_access: McpDataAccess,
        audit_sink: AuditSink,
        permission_guard: ToolPermissionGuard | None = None,
    ) -> None:
        self._data_access = data_access
        self._audit_sink = audit_sink
        self._permission_guard = permission_guard or ToolPermissionGuard()
        self._handlers: dict[ToolName, Callable[[ToolContext, dict[str, Any]], dict[str, Any]]] = {
            "search_documents": self._search_documents,
            "get_document": self._get_document,
            "search_chunks": self._search_chunks,
            "get_entities": self._get_entities,
            "get_audit_logs": self._get_audit_logs,
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [definition.to_mcp_dict() for definition in TOOL_DEFINITIONS.values()]

    def call_tool(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> ToolResult:
        args = arguments or {}
        if tool_name not in self._handlers:
            observability.capture_message(
                "Unknown MCP tool requested.",
                operation="mcp.tool_call",
                context={"tool_name": tool_name, "tenant_id": context.tenant_id},
            )
            return ToolResult(
                tool="search_documents",
                status="error",
                data={},
                error=f"Unknown tool: {tool_name}",
            )

        typed_name = tool_name  # narrowed after membership check

        # --- Pre-call: info breadcrumb with PII-scrubbed params ---
        scrubbed_params_summary = truncate(
            str(scrub_pii({k: str(v) if isinstance(v, str) else "<non-string>" for k, v in args.items()})),
            256,
        )
        observability.add_breadcrumb(
            message=f"MCP tool call: {tool_name}",
            category="mcp",
            level="info",
            data={"tool_name": tool_name, "params_summary": scrubbed_params_summary},
        )

        start_time = time.monotonic()

        try:
            self._permission_guard.assert_allowed(context, typed_name)  # type: ignore[arg-type]
            data = self._handlers[typed_name](context, args)  # type: ignore[index]

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # --- Timeout detection ---
            if elapsed_ms > 30_000:
                observability.capture_message(
                    f"MCP tool call timeout: {tool_name}",
                    operation="mcp.tool_timeout",
                    context={
                        "tool_name": tool_name,
                        "elapsed_ms": elapsed_ms,
                        "timeout_ms": 30_000.0,
                    },
                    level="warning",
                )

            audit_id = self._audit_sink.record_tool_call(
                context, tool_name, _audit_safe_arguments(args), "ok"
            )
            result = ToolResult(
                tool=typed_name,  # type: ignore[arg-type]
                status="ok",
                data=data,
                audit_id=audit_id,
            )
        except PermissionDeniedError as exc:
            observability.capture_exception(
                exc,
                operation="mcp.permission_denied",
                context={"tool_name": tool_name, "tenant_id": context.tenant_id},
            )
            audit_id = self._audit_sink.record_tool_call(
                context, tool_name, _audit_safe_arguments(args), "denied"
            )
            result = ToolResult(
                tool=typed_name,  # type: ignore[arg-type]
                status="denied",
                data={},
                audit_id=audit_id,
                error=str(exc),
            )
        except ToolValidationError as exc:
            observability.capture_exception(
                exc,
                operation="mcp.validation_error",
                context={"tool_name": tool_name, "tenant_id": context.tenant_id},
            )
            audit_id = self._audit_sink.record_tool_call(
                context, tool_name, _audit_safe_arguments(args), "error"
            )
            result = ToolResult(
                tool=typed_name,  # type: ignore[arg-type]
                status="error",
                data={},
                audit_id=audit_id,
                error=str(exc),
            )
        except Exception as exc:
            scrubbed_params = truncate(
                str(scrub_pii({k: str(v) if isinstance(v, str) else "<non-string>" for k, v in args.items()})),
                1024,
            )
            observability.capture_exception(
                exc,
                operation="mcp.tool_call",
                context={
                    "tool_name": tool_name,
                    "tenant_id": context.tenant_id,
                    "actor_user_id": context.actor_user_id,
                    "scrubbed_params": scrubbed_params,
                },
            )
            audit_id = self._audit_sink.record_tool_call(
                context, tool_name, _audit_safe_arguments(args), "error"
            )
            result = ToolResult(
                tool=typed_name,  # type: ignore[arg-type]
                status="error",
                data={},
                audit_id=audit_id,
                error=str(exc),
            )

        # --- Post-call: warning breadcrumb for error/denied results ---
        if result.status in ("error", "denied"):
            observability.add_breadcrumb(
                message=f"MCP tool call returned error: {tool_name}",
                category="mcp",
                level="warning",
                data={
                    "tool_name": tool_name,
                    "error_status": result.status,
                    "error_message": truncate(result.error or "", 512),
                },
            )

        return result

    def _search_documents(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        query = _required_string(args, "query")
        limit = _bounded_int(args.get("limit", 10), "limit", 1, 20)
        status = _optional_string(args.get("status"), "status")
        documents = self._data_access.search_documents(context, query, limit, status)
        return {"documents": [document.__dict__ for document in documents]}

    def _get_document(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        document_id = _required_string(args, "document_id")
        document = self._data_access.get_document(context, document_id)
        return {"document": document.__dict__ if document else None}

    def _search_chunks(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        query = _required_string(args, "query")
        limit = _bounded_int(args.get("limit", 10), "limit", 1, 20)
        document_id = _optional_string(args.get("document_id"), "document_id")
        chunks = self._data_access.search_chunks(context, query, limit, document_id)
        return {"chunks": [chunk.__dict__ for chunk in chunks]}

    def _get_entities(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        document_id = _required_string(args, "document_id")
        labels = _optional_string_list(args.get("labels"), "labels")
        limit = _bounded_int(args.get("limit", 50), "limit", 1, 100)
        entities = self._data_access.get_entities(context, document_id, labels, limit)
        return {"entities": [entity.__dict__ for entity in entities]}

    def _get_audit_logs(self, context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        resource_type = _optional_string(args.get("resource_type"), "resource_type")
        resource_id = _optional_string(args.get("resource_id"), "resource_id")
        limit = _bounded_int(args.get("limit", 50), "limit", 1, 100)
        logs = self._data_access.get_audit_logs(context, resource_type, resource_id, limit)
        return {"audit_logs": [log.__dict__ for log in logs]}


def _required_string(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolValidationError(f"{key} is required.")
    return value.strip()


def _optional_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolValidationError(f"{key} must be a non-empty string.")
    return value.strip()


def _optional_string_list(value: object, key: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ToolValidationError(f"{key} must be a list of strings.")
    return [item.strip() for item in value]


def _bounded_int(value: object, key: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolValidationError(f"{key} must be an integer.")
    if value < minimum or value > maximum:
        raise ToolValidationError(f"{key} must be between {minimum} and {maximum}.")
    return value


def _audit_safe_arguments(args: dict[str, Any]) -> dict[str, object]:
    allowed: dict[str, object] = {}
    for key, value in args.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            allowed[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            allowed[key] = list(value)
        else:
            allowed[key] = "<redacted>"
    return allowed
