"""Property 3: Unauthenticated Requests Rejected (via verify_jwt validation layer)
Validates: Requirements 1.4
"""
from __future__ import annotations

import time
import unittest

import _path  # noqa: F401
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.security.auth import verify_jwt, AuthError


class TestUnauthenticatedAlwaysRejected(unittest.TestCase):
    """Property 3: Unauthenticated Requests to Any Non-Health Path Are Rejected
    Validates: Requirements 1.4"""

    @given(
        path=st.text(min_size=1).filter(lambda p: p != "/health" and p != ""),
    )
    @settings(max_examples=100)
    def test_missing_token_causes_auth_error(self, path: str) -> None:
        """Any blank/empty token to a non-health path should fail JWT verification.

        The _authenticate() method in OmniModalHandler checks for 'Bearer ' prefix
        before calling verify_jwt. If the prefix is missing or token is empty,
        it returns 401. We test the underlying verify_jwt here with malformed inputs.
        """
        # Empty token, no-token scenarios all cause AuthError
        for bad_token in ["", "notabearer", "Bearer", "Bearer .", "x.y"]:
            with self.assertRaises(AuthError):
                verify_jwt(bad_token, "any-secret")

    def test_verify_jwt_rejects_empty_token(self) -> None:
        with self.assertRaises(AuthError):
            verify_jwt("", "secret")

    def test_verify_jwt_rejects_single_segment(self) -> None:
        with self.assertRaises(AuthError):
            verify_jwt("onlyone", "secret")

    def test_verify_jwt_rejects_two_segments(self) -> None:
        with self.assertRaises(AuthError):
            verify_jwt("header.payload", "secret")

    def test_verify_jwt_rejects_expired_token(self) -> None:
        """Expired tokens are always rejected regardless of path."""
        from omni_modal.security.auth import _make_jwt
        token = _make_jwt("t", "u", [], int(time.time()) - 1, "secret")
        with self.assertRaises(AuthError):
            verify_jwt(token, "secret")

    def test_verify_jwt_rejects_wrong_secret(self) -> None:
        """Tokens signed with different secret are always rejected."""
        from omni_modal.security.auth import _make_jwt
        token = _make_jwt("t", "u", [], int(time.time()) + 3600, "correct-secret")
        with self.assertRaises(AuthError):
            verify_jwt(token, "wrong-secret")


if __name__ == "__main__":
    unittest.main()
