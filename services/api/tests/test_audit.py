from __future__ import annotations

import unittest

from hypothesis import given, settings
import hypothesis.strategies as st

import _path  # noqa: F401
from omni_modal.security.audit import InMemoryAuditSink, _scrub
from omni_modal.mcp.models import ToolContext


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


@given(n=st.integers(min_value=1, max_value=200))
@settings(max_examples=100)
def test_audit_id_monotonically_increasing(n):
    """Property 13: Audit Entry IDs Are Strictly Monotonically Increasing
    Validates: Requirements 7.7"""
    sink = InMemoryAuditSink()
    ctx = ToolContext(tenant_id="t1", actor_user_id="u1")
    for _ in range(n):
        sink.record_event(ctx, "test:action", "endpoint", None, "ok", {})
    ids = [e.id for e in sink.entries]
    assert ids == list(range(1, n + 1))


@given(
    d=st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.one_of(
            st.integers(),
            st.booleans(),
            st.floats(allow_nan=False),
            st.text(),
            st.none(),
        ),
    )
)
@settings(max_examples=100)
def test_scrub_preserves_primitives_redacts_strings(d):
    """Property 14: Audit Scrubbing Preserves Primitives and Redacts Strings
    Validates: Requirements 9.3"""
    result = _scrub(d)
    for key, orig_val in d.items():
        scrubbed_val = result[key]
        if isinstance(orig_val, str):
            assert scrubbed_val == "<scrubbed>"
        elif isinstance(orig_val, bool):
            # bool check before int (bool is subclass of int)
            assert scrubbed_val == orig_val
        elif isinstance(orig_val, (int, float)) or orig_val is None:
            assert scrubbed_val == orig_val


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestRecordToolCall(unittest.TestCase):
    def test_creates_entry_with_expected_fields(self) -> None:
        sink = InMemoryAuditSink()
        ctx = ToolContext(tenant_id="acme", actor_user_id="user-42")
        audit_id = sink.record_tool_call(
            ctx,
            "search_documents",
            {"query": "annual report", "limit": 10},
            "ok",
        )
        self.assertEqual(audit_id, "1")
        entry = sink.entries[0]
        self.assertEqual(entry.action, "tool:search_documents")
        self.assertEqual(entry.tenant_id, "acme")
        self.assertEqual(entry.actor_user_id, "user-42")
        self.assertEqual(entry.status, "ok")
        self.assertEqual(entry.resource_type, "tool")
        self.assertEqual(entry.resource_id, "search_documents")

    def test_arguments_are_scrubbed_in_metadata(self) -> None:
        sink = InMemoryAuditSink()
        ctx = ToolContext(tenant_id="t1", actor_user_id="u1")
        sink.record_tool_call(
            ctx,
            "search_documents",
            {"query": "secret text", "limit": 5},
            "ok",
        )
        scrubbed_args = sink.entries[0].metadata["arguments"]
        self.assertIsInstance(scrubbed_args, dict)
        self.assertEqual(scrubbed_args["query"], "<scrubbed>")  # type: ignore[index]
        self.assertEqual(scrubbed_args["limit"], 5)  # type: ignore[index]


class TestRecordEvent(unittest.TestCase):
    def test_with_context(self) -> None:
        sink = InMemoryAuditSink()
        ctx = ToolContext(tenant_id="tenant-a", actor_user_id="user-1")
        audit_id = sink.record_event(
            ctx, "auth:login", "endpoint", "/query", "ok", {"ip": "127.0.0.1"}
        )
        self.assertEqual(audit_id, "1")
        entry = sink.entries[0]
        self.assertEqual(entry.tenant_id, "tenant-a")
        self.assertEqual(entry.actor_user_id, "user-1")
        self.assertEqual(entry.action, "auth:login")

    def test_with_none_context_uses_system_defaults(self) -> None:
        sink = InMemoryAuditSink()
        audit_id = sink.record_event(
            None, "system:startup", "service", None, "ok", {}
        )
        self.assertEqual(audit_id, "1")
        entry = sink.entries[0]
        self.assertEqual(entry.tenant_id, "system")
        self.assertIsNone(entry.actor_user_id)


class TestEntriesProperty(unittest.TestCase):
    def test_entries_returns_copy_not_reference(self) -> None:
        sink = InMemoryAuditSink()
        ctx = ToolContext(tenant_id="t1", actor_user_id="u1")
        sink.record_event(ctx, "action", "resource", None, "ok", {})

        snapshot = sink.entries
        # Mutating the returned list must not affect internal state
        snapshot.clear()
        self.assertEqual(len(sink.entries), 1)


class TestScrubFunction(unittest.TestCase):
    def test_string_values_are_redacted(self) -> None:
        result = _scrub({"name": "Alice", "token": "s3cr3t"})
        self.assertEqual(result["name"], "<scrubbed>")
        self.assertEqual(result["token"], "<scrubbed>")

    def test_primitive_values_are_preserved(self) -> None:
        result = _scrub({"count": 42, "ratio": 3.14, "active": True, "missing": None})
        self.assertEqual(result["count"], 42)
        self.assertAlmostEqual(result["ratio"], 3.14)  # type: ignore[arg-type]
        self.assertEqual(result["active"], True)
        self.assertIsNone(result["missing"])

    def test_bool_preserved_not_treated_as_int(self) -> None:
        result = _scrub({"flag": True})
        self.assertIs(result["flag"], True)


class TestMonotonicIdsWithMixedCalls(unittest.TestCase):
    def test_ids_monotonically_increasing_across_mixed_calls(self) -> None:
        sink = InMemoryAuditSink()
        ctx = ToolContext(tenant_id="t1", actor_user_id="u1")
        sink.record_tool_call(ctx, "search_documents", {}, "ok")
        sink.record_event(ctx, "auth:check", "endpoint", "/query", "ok", {})
        sink.record_tool_call(ctx, "get_document", {"id": 1}, "ok")
        sink.record_event(None, "system:event", "service", None, "ok", {})

        ids = [e.id for e in sink.entries]
        self.assertEqual(ids, [1, 2, 3, 4])
        # strictly increasing
        for a, b in zip(ids, ids[1:]):
            self.assertLess(a, b)


if __name__ == "__main__":
    unittest.main()
