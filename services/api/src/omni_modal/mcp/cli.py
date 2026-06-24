from __future__ import annotations

import os
import sys

from omni_modal.env_loader import load_dotenv
from omni_modal.mcp.repositories import InMemoryMcpRepository, PostgresMcpRepository
from omni_modal.mcp.server import McpServer
from omni_modal.mcp.tools import McpToolRouter
from omni_modal.security.document_access import DocumentAccessGuard

# Honour the repository .env so DATABASE_URL is picked up when the MCP server
# is launched directly (e.g. by an MCP client) without a pre-exported env.
load_dotenv()


def build_router() -> McpToolRouter:
    """Build the MCP tool router with document-level access enforcement.

    - With DATABASE_URL set: use the Postgres-backed repository.
    - Without it: fall back to an empty in-memory repository so the server can
      still start for local inspection (it simply returns no documents).

    In both cases the data-access layer is wrapped in a ``DocumentAccessGuard``
    so visibility rules are enforced on every tool call, and the repository
    itself is used as the audit sink (it implements ``record_tool_call``).
    """
    if os.environ.get("DATABASE_URL"):
        repository = PostgresMcpRepository()
        audit_sink = repository
    else:
        print(
            "DATABASE_URL is not set; starting MCP server with an empty "
            "in-memory repository (no documents will be returned).",
            file=sys.stderr,
        )
        repository = InMemoryMcpRepository()
        audit_sink = repository

    guarded = DocumentAccessGuard(repository, audit_sink)
    return McpToolRouter(guarded, audit_sink)


def main() -> int:
    McpServer(build_router()).serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
