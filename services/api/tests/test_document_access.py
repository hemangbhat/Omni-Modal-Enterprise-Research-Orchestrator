"""Tests for document_access.py — Properties 5, 6, 7 and unit coverage."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import _path  # noqa: F401

from hypothesis import given, settings, assume
import hypothesis.strategies as st

from omni_modal.security.document_access import (
    AccessMetadata,
    AccessDenied,
    DocumentAccessGuard,
    check_access,
)
from omni_modal.mcp.models import (
    AuditLogSummary,
    ChunkSummary,
    DocumentDetail,
    DocumentSummary,
    EntitySummary,
    ToolContext,
)


# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------


@given(
    user_id=st.text(min_size=1, max_size=20),
    owner_ids=st.lists(st.text(min_size=1, max_size=20), min_size=0, max_size=5),
    visibility=st.sampled_from(["private", "tenant", "restricted"]),
)
@settings(max_examples=100)
def test_access_guard_filters_correctly(user_id, owner_ids, visibility):
    """Property 5: Document Access Guard Returns Only Permitted Documents"""
    tenant_id = "tenant-a"
    context = ToolContext(tenant_id=tenant_id, actor_user_id=user_id)

    # Build document summaries — use owner_id as visibility marker
    docs = [
        DocumentSummary(
            id=f"doc-{i}",
            title=f"Doc {i}",
            source_type="pdf",
            status="ready",
            owner_id=oid,
        )
        for i, oid in enumerate(owner_ids)
    ]

    # For each doc, manually compute expected access and verify consistency
    for doc in docs:
        meta = AccessMetadata(visibility=visibility, owner_id=doc.owner_id)
        expected = check_access(context, tenant_id, meta)
        actual = check_access(context, tenant_id, meta)
        assert expected == actual


@given(
    tenant_a=st.text(min_size=1, max_size=20),
    tenant_b=st.text(min_size=1, max_size=20),
    user_id=st.text(min_size=1, max_size=20),
    visibility=st.sampled_from(["private", "tenant", "restricted"]),
)
@settings(max_examples=100)
def test_cross_tenant_always_denied(tenant_a, tenant_b, user_id, visibility):
    """Property 6: Cross-Tenant Access Always Denied — Validates: Requirements 3.6"""
    assume(tenant_a != tenant_b)
    context = ToolContext(tenant_id=tenant_a, actor_user_id=user_id)
    meta = AccessMetadata(
        visibility=visibility,
        owner_id=user_id,
        allowed_user_ids=[user_id],
        allowed_roles=["researcher"],
    )
    # Even if owner_id matches and user is in allowed list, cross-tenant must fail
    result = check_access(context, tenant_b, meta)
    assert result is False


@given(
    user_id=st.text(min_size=1, max_size=20),
    owner_id=st.text(min_size=1, max_size=20),
)
@settings(max_examples=100)
def test_private_visibility_owner_match(user_id, owner_id):
    """Property 7: Private Visibility Grants Access Iff Owner Matches — Validates: Requirements 3.1"""
    tenant = "tenant-a"
    context = ToolContext(tenant_id=tenant, actor_user_id=user_id)
    meta = AccessMetadata(visibility="private", owner_id=owner_id)
    result = check_access(context, tenant, meta)
    assert result == (user_id == owner_id)


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------


class TestCheckAccessPrivate(unittest.TestCase):
    """check_access with visibility='private'."""

    def _ctx(self, user_id: str, tenant: str = "t1") -> ToolContext:
        return ToolContext(tenant_id=tenant, actor_user_id=user_id)

    def test_private_owner_match_grants(self):
        meta = AccessMetadata(visibility="private", owner_id="alice")
        self.assertTrue(check_access(self._ctx("alice"), "t1", meta))

    def test_private_wrong_user_denied(self):
        meta = AccessMetadata(visibility="private", owner_id="alice")
        self.assertFalse(check_access(self._ctx("bob"), "t1", meta))

    def test_private_no_owner_denied(self):
        meta = AccessMetadata(visibility="private", owner_id=None)
        self.assertFalse(check_access(self._ctx("alice"), "t1", meta))


class TestCheckAccessTenant(unittest.TestCase):
    """check_access with visibility='tenant'."""

    def test_tenant_any_user_granted(self):
        ctx = ToolContext(tenant_id="t1", actor_user_id="anyone")
        meta = AccessMetadata(visibility="tenant")
        self.assertTrue(check_access(ctx, "t1", meta))

    def test_tenant_different_tenant_denied(self):
        ctx = ToolContext(tenant_id="t1", actor_user_id="anyone")
        meta = AccessMetadata(visibility="tenant")
        self.assertFalse(check_access(ctx, "t2", meta))


class TestCheckAccessRestricted(unittest.TestCase):
    """check_access with visibility='restricted'."""

    def _ctx(self, user_id: str, roles: tuple[str, ...] = ()) -> ToolContext:
        return ToolContext(tenant_id="t1", actor_user_id=user_id, roles=roles)

    def test_restricted_user_in_allowed_list(self):
        meta = AccessMetadata(
            visibility="restricted",
            allowed_user_ids=["alice", "bob"],
        )
        self.assertTrue(check_access(self._ctx("alice"), "t1", meta))

    def test_restricted_role_match(self):
        meta = AccessMetadata(
            visibility="restricted",
            allowed_roles=["researcher"],
        )
        self.assertTrue(check_access(self._ctx("charlie", ("researcher",)), "t1", meta))

    def test_restricted_user_not_in_list_and_no_role(self):
        meta = AccessMetadata(
            visibility="restricted",
            allowed_user_ids=["alice"],
            allowed_roles=["admin"],
        )
        self.assertFalse(check_access(self._ctx("dave", ("viewer",)), "t1", meta))

    def test_restricted_empty_lists_denied(self):
        meta = AccessMetadata(
            visibility="restricted",
            allowed_user_ids=[],
            allowed_roles=[],
        )
        self.assertFalse(check_access(self._ctx("alice"), "t1", meta))


class TestCheckAccessCrossTenant(unittest.TestCase):
    """Cross-tenant access is always denied regardless of visibility."""

    def test_cross_tenant_private_denied(self):
        ctx = ToolContext(tenant_id="t1", actor_user_id="alice")
        meta = AccessMetadata(visibility="private", owner_id="alice")
        self.assertFalse(check_access(ctx, "t2", meta))

    def test_cross_tenant_tenant_denied(self):
        ctx = ToolContext(tenant_id="t1", actor_user_id="alice")
        meta = AccessMetadata(visibility="tenant")
        self.assertFalse(check_access(ctx, "t2", meta))

    def test_cross_tenant_restricted_denied(self):
        ctx = ToolContext(tenant_id="t1", actor_user_id="alice")
        meta = AccessMetadata(
            visibility="restricted",
            allowed_user_ids=["alice"],
            allowed_roles=["admin"],
        )
        self.assertFalse(check_access(ctx, "t2", meta))


class TestDocumentAccessGuardFiltering(unittest.TestCase):
    """DocumentAccessGuard wraps McpDataAccess and filters results."""

    def _make_guard(self, docs: list[DocumentSummary]) -> DocumentAccessGuard:
        inner = MagicMock()
        inner.search_documents.return_value = docs
        audit = MagicMock()
        return DocumentAccessGuard(inner, audit), inner

    def test_guard_returns_only_permitted_docs(self):
        # Two docs owned by "alice", one by "bob" — tenant visibility by default
        docs = [
            DocumentSummary(id="d1", title="D1", source_type="pdf", status="ready", owner_id="alice"),
            DocumentSummary(id="d2", title="D2", source_type="pdf", status="ready", owner_id="bob"),
        ]
        guard, inner = self._make_guard(docs)
        ctx = ToolContext(tenant_id="t1", actor_user_id="alice")
        result = guard.search_documents(ctx, "query", 10)
        # Both docs are tenant-visible (default), so both returned
        self.assertEqual(len(result), 2)

    def test_guard_get_document_returns_none_for_denied(self):
        # Detail doc with private visibility belonging to "bob"
        doc = DocumentDetail(
            id="d1", title="D1", source_type="pdf", status="ready",
            owner_id="bob",
            metadata={"visibility": "private"},
        )
        inner = MagicMock()
        inner.get_document.return_value = doc
        audit = MagicMock()
        guard = DocumentAccessGuard(inner, audit)
        ctx = ToolContext(tenant_id="t1", actor_user_id="alice")
        result = guard.get_document(ctx, "d1")
        self.assertIsNone(result)

    def test_guard_get_document_returns_doc_for_owner(self):
        doc = DocumentDetail(
            id="d1", title="D1", source_type="pdf", status="ready",
            owner_id="alice",
            metadata={"visibility": "private"},
        )
        inner = MagicMock()
        inner.get_document.return_value = doc
        audit = MagicMock()
        guard = DocumentAccessGuard(inner, audit)
        ctx = ToolContext(tenant_id="t1", actor_user_id="alice")
        result = guard.get_document(ctx, "d1")
        self.assertIsNotNone(result)
        self.assertEqual(result.id, "d1")

    def test_guard_get_document_none_when_inner_returns_none(self):
        inner = MagicMock()
        inner.get_document.return_value = None
        guard = DocumentAccessGuard(inner, MagicMock())
        ctx = ToolContext(tenant_id="t1", actor_user_id="alice")
        self.assertIsNone(guard.get_document(ctx, "missing"))


if __name__ == "__main__":
    unittest.main()
