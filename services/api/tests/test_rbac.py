"""Tests for RBAC endpoint guard (security/rbac.py).

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.6**
"""
from __future__ import annotations

import unittest

import _path  # noqa: F401
import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.security.rbac import assert_endpoint_roles, ENDPOINT_ROLES, RbacError


# ---------------------------------------------------------------------------
# 2.1  Property test — Property 4: RBAC Raises Error Iff Role Intersection
#      Is Empty
# ---------------------------------------------------------------------------

@given(
    roles=st.frozensets(st.text(min_size=1, max_size=20)),
    path=st.sampled_from(list(ENDPOINT_ROLES.keys())),
)
@settings(max_examples=200)
def test_rbac_role_intersection(roles: frozenset[str], path: str) -> None:
    """Property 4: RBAC Raises Error Iff Role Intersection Is Empty
    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.6"""
    required = ENDPOINT_ROLES[path]
    has_required = bool(frozenset(roles) & required)

    if has_required:
        # Should NOT raise
        assert_endpoint_roles(path, tuple(roles))
    else:
        # MUST raise
        with pytest.raises(RbacError):
            assert_endpoint_roles(path, tuple(roles))


# ---------------------------------------------------------------------------
# 2.2  Unit tests for assert_endpoint_roles
# ---------------------------------------------------------------------------

class TestAssertEndpointRoles(unittest.TestCase):

    # /query endpoint
    def test_query_researcher_allowed(self) -> None:
        assert_endpoint_roles("/query", ("researcher",))  # no error expected

    def test_query_admin_allowed(self) -> None:
        assert_endpoint_roles("/query", ("admin",))  # no error expected

    def test_query_auditor_denied(self) -> None:
        with self.assertRaises(RbacError):
            assert_endpoint_roles("/query", ("auditor",))

    def test_query_empty_roles_denied(self) -> None:
        with self.assertRaises(RbacError):
            assert_endpoint_roles("/query", ())

    # /ingest/local endpoint
    def test_ingest_local_researcher_allowed(self) -> None:
        assert_endpoint_roles("/ingest/local", ("researcher",))

    def test_ingest_local_admin_allowed(self) -> None:
        assert_endpoint_roles("/ingest/local", ("admin",))

    def test_ingest_local_auditor_denied(self) -> None:
        with self.assertRaises(RbacError):
            assert_endpoint_roles("/ingest/local", ("auditor",))

    # /query/stream endpoint
    def test_query_stream_researcher_allowed(self) -> None:
        assert_endpoint_roles("/query/stream", ("researcher",))

    # Unknown path — always passes (404 handled by normal request handler)
    def test_unknown_path_any_roles_no_error(self) -> None:
        result = assert_endpoint_roles("/unknown-path", ("anonymous",))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
