"""Tests for the optional adapters (local-first defaults)."""
from __future__ import annotations

import os
import tempfile
import unittest

import _path  # noqa: F401

from omni_modal.saas.adapters.analytics import (
    InMemoryAnalyticsAdapter,
    select_analytics_adapter,
)
from omni_modal.saas.adapters.email import ConsoleEmailAdapter, select_email_adapter
from omni_modal.saas.adapters.storage import LocalStorageAdapter, select_storage_adapter


class TestStorageAdapter(unittest.TestCase):
    def test_put_get_delete_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = LocalStorageAdapter(base_dir=d)
            obj = store.put("docs/a.txt", b"hello")
            self.assertEqual(obj.size_bytes, 5)
            self.assertEqual(obj.backend, "local")
            self.assertEqual(store.get("docs/a.txt"), b"hello")
            self.assertTrue(store.delete("docs/a.txt"))
            self.assertFalse(store.delete("docs/a.txt"))

    def test_path_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = LocalStorageAdapter(base_dir=d)
            # ".." is neutralized; the write stays inside base_dir
            obj = store.put("../escape.txt", b"x")
            self.assertTrue(obj.location.startswith(os.path.realpath(d)))

    def test_select_defaults_to_local_without_s3(self) -> None:
        prev = os.environ.pop("S3_BUCKET", None)
        try:
            self.assertEqual(select_storage_adapter().backend, "local")
        finally:
            if prev is not None:
                os.environ["S3_BUCKET"] = prev


class TestEmailAdapter(unittest.TestCase):
    def test_console_records_sent(self) -> None:
        adapter = ConsoleEmailAdapter()
        adapter.send(to="x@x.com", subject="Hi", body="Body")
        self.assertEqual(len(adapter.sent), 1)
        self.assertEqual(adapter.sent[0].subject, "Hi")

    def test_select_defaults_to_console(self) -> None:
        prev = os.environ.pop("RESEND_API_KEY", None)
        try:
            self.assertEqual(select_email_adapter().backend, "console")
        finally:
            if prev is not None:
                os.environ["RESEND_API_KEY"] = prev


class TestAnalyticsAdapter(unittest.TestCase):
    def test_capture_and_counts(self) -> None:
        adapter = InMemoryAnalyticsAdapter()
        adapter.capture(event="query", tenant_id="t1")
        adapter.capture(event="query", tenant_id="t1")
        adapter.capture(event="upload", tenant_id="t1")
        self.assertEqual(adapter.event_counts(), {"query": 2, "upload": 1})
        self.assertEqual(len(adapter.recent()), 3)

    def test_select_defaults_to_in_memory(self) -> None:
        prev = os.environ.pop("POSTHOG_API_KEY", None)
        try:
            self.assertEqual(select_analytics_adapter().backend, "in-memory")
        finally:
            if prev is not None:
                os.environ["POSTHOG_API_KEY"] = prev


if __name__ == "__main__":
    unittest.main()
