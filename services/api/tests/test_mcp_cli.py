import importlib
import os
import unittest
from unittest import mock

import _path  # noqa: F401
from omni_modal.mcp import cli as cli_module
from omni_modal.mcp.cli import build_router
from omni_modal.security.document_access import DocumentAccessGuard


class McpCliBootstrapTests(unittest.TestCase):
    def test_importing_the_cli_does_not_load_dotenv(self) -> None:
        # Importing a module must not pull the repository .env into the
        # process: only launching the server (main) may do that.
        self.addCleanup(importlib.reload, cli_module)
        with mock.patch("omni_modal.env_loader.load_dotenv") as loader:
            importlib.reload(cli_module)
        loader.assert_not_called()

    def test_main_loads_dotenv_before_starting_the_server(self) -> None:
        calls: list[str] = []
        with mock.patch.object(cli_module, "load_dotenv", side_effect=lambda: calls.append("dotenv")),              mock.patch.object(cli_module, "build_router", side_effect=lambda: calls.append("router")),              mock.patch.object(cli_module, "McpServer") as server:
            self.assertEqual(cli_module.main(), 0)
        self.assertEqual(calls, ["dotenv", "router"])
        server.return_value.serve_stdio.assert_called_once()

    def test_build_router_without_database_url_uses_guarded_in_memory(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            router = build_router()
        # The router's data access must be wrapped in the access guard.
        self.assertIsInstance(router._data_access, DocumentAccessGuard)

    def test_build_router_with_database_url_uses_guarded_postgres(self) -> None:
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgres://x"}, clear=False):
            router = build_router()
        self.assertIsInstance(router._data_access, DocumentAccessGuard)
        # Audit sink is the underlying repository (implements record_tool_call).
        self.assertTrue(hasattr(router._audit_sink, "record_tool_call"))


if __name__ == "__main__":
    unittest.main()
