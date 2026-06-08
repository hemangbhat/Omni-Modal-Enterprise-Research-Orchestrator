import os
import unittest
from unittest import mock

import _path  # noqa: F401
from omni_modal.mcp.cli import build_router
from omni_modal.security.document_access import DocumentAccessGuard


class McpCliBootstrapTests(unittest.TestCase):
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
