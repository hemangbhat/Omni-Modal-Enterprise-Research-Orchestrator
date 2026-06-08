from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any, TextIO

from omni_modal.mcp.models import ToolContext
from omni_modal.mcp.tools import McpToolRouter


class McpProtocolError(ValueError):
    pass


class McpServer:
    def __init__(self, router: McpToolRouter) -> None:
        self._router = router

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error_response(request_id, -32602, "params must be an object.")

        try:
            if method == "tools/list":
                return _success_response(request_id, {"tools": self._router.list_tools()})
            if method == "tools/call":
                context = context_from_params(params)
                tool_name = _required_string(params, "name")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise McpProtocolError("arguments must be an object.")
                result = self._router.call_tool(context, tool_name, arguments)
                return _success_response(
                    request_id,
                    {
                        "content": [
                            {
                                "type": "json",
                                "json": result.to_json_dict(),
                            }
                        ],
                        "isError": result.status != "ok",
                    },
                )
        except McpProtocolError as exc:
            return _error_response(request_id, -32602, str(exc))

        return _error_response(request_id, -32601, f"Unsupported method: {method}")

    def serve_stdio(
        self,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        source = input_stream or sys.stdin
        sink = output_stream or sys.stdout
        for line in source:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise McpProtocolError("Request must be a JSON object.")
                response = self.handle_request(request)
            except (json.JSONDecodeError, McpProtocolError) as exc:
                response = _error_response(None, -32700, str(exc))
            sink.write(json.dumps(response, sort_keys=True) + "\n")
            sink.flush()


def context_from_params(params: dict[str, Any]) -> ToolContext:
    raw_context = params.get("context")
    if not isinstance(raw_context, dict):
        raise McpProtocolError("context is required.")

    tenant_id = _required_string(raw_context, "tenant_id")
    actor_user_id = _required_string(raw_context, "actor_user_id")
    raw_roles = raw_context.get("roles", [])
    if not isinstance(raw_roles, list) or not all(isinstance(role, str) for role in raw_roles):
        raise McpProtocolError("context.roles must be a list of strings.")

    request_id = raw_context.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise McpProtocolError("context.request_id must be a string.")

    return ToolContext(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        roles=tuple(raw_roles),
        request_id=request_id,
    )


def _required_string(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpProtocolError(f"{key} is required.")
    return value.strip()


def _success_response(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(
    request_id: object, code: int, message: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def context_to_dict(context: ToolContext) -> dict[str, Any]:
    return asdict(context)
