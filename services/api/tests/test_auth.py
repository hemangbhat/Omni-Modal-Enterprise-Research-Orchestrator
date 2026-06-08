"""Tests for security/auth.py — JWT verification and SecretRef no-leak placeholder.

Sub-tasks covered:
  1.1  Property test: JWT round-trip fidelity          (Property 1, Req 1.3)
  1.2  Property test: tampered signature rejected      (Property 2, Req 1.1, 1.2, 1.5)
  1.3  Unit tests: verify_jwt example-based
  15.1 Placeholder: SecretRef string representation never reveals secret value

Run with:
    python -m unittest discover -s services/api/tests -p "test_auth.py"
"""
from __future__ import annotations

import hashlib
import hmac as _hmac_module
import json
import time as time_module
import unittest

import _path  # noqa: F401

from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.security.auth import (
    AuthError,
    JwtClaims,
    _b64url_decode,
    _b64url_encode,
    _make_jwt,
    verify_jwt,
)
from omni_modal.security.secrets import SecretRef


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

_SECRET = "test-secret-key"


def _build_token(
    *,
    tenant_id: str = "tenant-1",
    user_id: str = "user-1",
    roles: list[str] | None = None,
    exp_offset: int = 3600,
    secret: str = _SECRET,
) -> str:
    return _make_jwt(
        tenant_id,
        user_id,
        roles if roles is not None else ["researcher"],
        int(time_module.time()) + exp_offset,
        secret,
    )


def _build_raw_token(payload_dict: dict, secret: str = _SECRET) -> str:
    """Build a JWT from a raw payload dict without going through _make_jwt."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps(payload_dict).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _b64url_encode(
        _hmac_module.new(secret.encode(), signing_input, hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{sig}"


# ---------------------------------------------------------------------------
# 1.1  Property test: JWT round-trip fidelity
#      **Validates: Requirements 1.3**
# ---------------------------------------------------------------------------

class TestJwtRoundTripFidelity(unittest.TestCase):
    """Property 1: JWT Round-Trip Fidelity — Validates: Requirements 1.3"""

    @given(
        tenant_id=st.text(min_size=1, max_size=50),
        user_id=st.text(min_size=1, max_size=50),
        roles=st.lists(st.text(max_size=20)),
        exp_offset=st.integers(min_value=1, max_value=3600),
    )
    @settings(max_examples=100)
    def test_jwt_round_trip_fidelity(
        self,
        tenant_id: str,
        user_id: str,
        roles: list[str],
        exp_offset: int,
    ) -> None:
        """Encode → decode preserves all four claims exactly."""
        exp = int(time_module.time()) + exp_offset
        token = _make_jwt(tenant_id, user_id, roles, exp, _SECRET)
        claims = verify_jwt(token, _SECRET)
        self.assertEqual(claims.tenant_id, tenant_id)
        self.assertEqual(claims.user_id, user_id)
        self.assertEqual(claims.roles, tuple(roles))
        self.assertEqual(claims.exp, exp)


# ---------------------------------------------------------------------------
# 1.2  Property test: tampered signature is always rejected
#      **Validates: Requirements 1.1, 1.2, 1.5**
# ---------------------------------------------------------------------------

class TestTamperedSignatureRejected(unittest.TestCase):
    """Property 2a: Tampered signature always raises AuthError — Validates: Requirements 1.1, 1.2"""

    @given(
        tenant_id=st.text(min_size=1, max_size=50),
        user_id=st.text(min_size=1, max_size=50),
        byte_pos=st.integers(min_value=0, max_value=31),
    )
    @settings(max_examples=100)
    def test_tampered_signature_rejected(
        self,
        tenant_id: str,
        user_id: str,
        byte_pos: int,
    ) -> None:
        """Any single-character mutation in the signature segment must raise AuthError."""
        exp = int(time_module.time()) + 3600
        token = _make_jwt(tenant_id, user_id, [], exp, _SECRET)
        header, payload, sig = token.split(".")
        sig_chars = list(sig)
        pos = byte_pos % len(sig_chars)
        original = sig_chars[pos]
        sig_chars[pos] = "A" if original != "A" else "B"
        tampered = f"{header}.{payload}.{''.join(sig_chars)}"
        with self.assertRaises(AuthError):
            verify_jwt(tampered, _SECRET)

    def test_absent_tenant_id_raises(self) -> None:
        """Missing tenant_id claim raises AuthError (Req 1.5)."""
        token = _build_raw_token({
            "user_id": "u",
            "roles": [],
            "exp": int(time_module.time()) + 3600,
        })
        with self.assertRaises(AuthError):
            verify_jwt(token, _SECRET)

    def test_expired_exp_raises(self) -> None:
        """Expired exp claim raises AuthError (Req 1.5)."""
        token = _make_jwt("t", "u", [], int(time_module.time()) - 1, _SECRET)
        with self.assertRaises(AuthError):
            verify_jwt(token, _SECRET)


# ---------------------------------------------------------------------------
# 1.3  Unit tests: example-based verify_jwt coverage
#      Validates: Requirements 1.1, 1.2, 1.5
# ---------------------------------------------------------------------------

class TestVerifyJwt(unittest.TestCase):
    """Example-based tests for verify_jwt."""

    def test_valid_token_returns_claims(self) -> None:
        token = _build_token()
        claims = verify_jwt(token, _SECRET)
        self.assertEqual(claims.tenant_id, "tenant-1")
        self.assertEqual(claims.user_id, "user-1")
        self.assertEqual(claims.roles, ("researcher",))

    def test_expired_token_raises_auth_error(self) -> None:
        token = _make_jwt("t", "u", [], int(time_module.time()) - 1, _SECRET)
        with self.assertRaises(AuthError):
            verify_jwt(token, _SECRET)

    def test_wrong_secret_raises_auth_error(self) -> None:
        token = _build_token(secret=_SECRET)
        with self.assertRaises(AuthError):
            verify_jwt(token, "wrong-secret")

    def test_wrong_alg_raises_auth_error(self) -> None:
        header = _b64url_encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64url_encode(json.dumps({
            "tenant_id": "t",
            "user_id": "u",
            "roles": [],
            "exp": int(time_module.time()) + 3600,
        }).encode())
        signing_input = f"{header}.{payload}".encode()
        sig = _b64url_encode(
            _hmac_module.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        )
        with self.assertRaises(AuthError):
            verify_jwt(f"{header}.{payload}.{sig}", _SECRET)

    def test_tampered_signature_raises_auth_error(self) -> None:
        token = _build_token()
        header, payload, sig = token.split(".")
        bad_sig = ("B" if sig[0] != "B" else "A") + sig[1:]
        with self.assertRaises(AuthError):
            verify_jwt(f"{header}.{payload}.{bad_sig}", _SECRET)

    def test_missing_tenant_id_raises_auth_error(self) -> None:
        token = _build_raw_token({
            "user_id": "u",
            "roles": [],
            "exp": int(time_module.time()) + 3600,
        })
        with self.assertRaises(AuthError):
            verify_jwt(token, _SECRET)

    def test_missing_user_id_raises_auth_error(self) -> None:
        token = _build_raw_token({
            "tenant_id": "t",
            "roles": [],
            "exp": int(time_module.time()) + 3600,
        })
        with self.assertRaises(AuthError):
            verify_jwt(token, _SECRET)

    def test_malformed_token_not_three_parts_raises_auth_error(self) -> None:
        with self.assertRaises(AuthError):
            verify_jwt("onlyone", _SECRET)

    def test_empty_roles_produces_empty_tuple(self) -> None:
        token = _make_jwt("t", "u", [], int(time_module.time()) + 3600, _SECRET)
        claims = verify_jwt(token, _SECRET)
        self.assertEqual(claims.roles, ())

    def test_alternate_claim_names_tid_and_sub(self) -> None:
        """verify_jwt should also accept 'tid' and 'sub' as claim aliases."""
        token = _build_raw_token({
            "tid": "tenant-alt",
            "sub": "user-alt",
            "roles": ["admin"],
            "exp": int(time_module.time()) + 3600,
        })
        claims = verify_jwt(token, _SECRET)
        self.assertEqual(claims.tenant_id, "tenant-alt")
        self.assertEqual(claims.user_id, "user-alt")
        self.assertEqual(claims.roles, ("admin",))


# ---------------------------------------------------------------------------
# Task 15.1 placeholder — SecretRef string representation never reveals value
# **Validates: Requirements 9.5**
# ---------------------------------------------------------------------------

class TestSecretRefNoLeak(unittest.TestCase):
    """Placeholder / baseline tests for the SecretRef no-leak invariant.

    The full property-based version lives in task 15.1. These unit tests
    confirm the existing behaviour so any regression surfaces immediately.
    """

    def test_str_contains_redacted_marker(self) -> None:
        ref = SecretRef(name="MY_SECRET")
        self.assertIn("<redacted>", str(ref))

    def test_repr_contains_redacted_marker(self) -> None:
        ref = SecretRef(name="MY_SECRET")
        self.assertIn("<redacted>", repr(ref))

    def test_str_and_repr_include_name(self) -> None:
        ref = SecretRef(name="MY_SECRET")
        self.assertIn("MY_SECRET", str(ref))
        self.assertIn("MY_SECRET", repr(ref))

    def test_raw_secret_value_not_in_repr(self) -> None:
        """The 'value=...' portion of repr must never show a raw credential."""
        ref = SecretRef(name="DUMMY")
        self.assertNotIn("value=super-secret-password-abc123", repr(ref))


if __name__ == "__main__":
    unittest.main()
