"""Tests for password hashing and the credential AccountService (Phase E).

Uses the in-memory account store (no DB) and includes a Hypothesis property
test for the password hash round-trip. The Postgres-backed path is exercised by
the integration smoke when DATABASE_URL is set.
"""

from __future__ import annotations

import time
import unittest

import _path  # noqa: F401
from hypothesis import given, settings
from hypothesis import strategies as st

from omni_modal.security.accounts import AccountError, AccountService, InMemoryAccountStore
from omni_modal.security.auth import _make_jwt, verify_jwt
from omni_modal.security.passwords import hash_password, verify_password


class PasswordHashingTests(unittest.TestCase):
    def test_hash_is_salted_and_verifiable(self) -> None:
        h1 = hash_password("correct horse battery staple")
        h2 = hash_password("correct horse battery staple")
        self.assertNotEqual(h1, h2, "per-password salt should make hashes differ")
        self.assertTrue(verify_password("correct horse battery staple", h1))
        self.assertTrue(verify_password("correct horse battery staple", h2))

    def test_wrong_password_rejected(self) -> None:
        h = hash_password("s3cretpassword")
        self.assertFalse(verify_password("wrong", h))
        self.assertFalse(verify_password("", h))

    def test_hash_never_contains_plaintext(self) -> None:
        secret = "myVerySecretValue123"
        self.assertNotIn(secret, hash_password(secret))

    @settings(max_examples=25, deadline=None)
    @given(st.text(min_size=1, max_size=128))
    def test_roundtrip_property(self, password: str) -> None:
        stored = hash_password(password)
        self.assertTrue(verify_password(password, stored))
        # A different password must not verify (unless equal).
        other = password + "x"
        self.assertFalse(verify_password(other, stored))


class AccountServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = AccountService(InMemoryAccountStore())

    def test_register_then_authenticate(self) -> None:
        acct = self.svc.register(email="Alice@Example.com", password="hunter2hunter")
        self.assertTrue(acct.tenant_id.startswith("org-"))
        self.assertIn("admin", acct.roles)
        # Case-insensitive email match on login.
        found = self.svc.authenticate(email="alice@example.com", password="hunter2hunter")
        self.assertIsNotNone(found)
        self.assertEqual(found.user_id, acct.user_id)

    def test_duplicate_email_rejected(self) -> None:
        self.svc.register(email="bob@example.com", password="password123")
        with self.assertRaises(AccountError):
            self.svc.register(email="bob@example.com", password="anotherpw1")

    def test_invalid_email_and_short_password_rejected(self) -> None:
        with self.assertRaises(AccountError):
            self.svc.register(email="not-an-email", password="password123")
        with self.assertRaises(AccountError):
            self.svc.register(email="c@example.com", password="short")

    def test_wrong_password_returns_none(self) -> None:
        self.svc.register(email="dora@example.com", password="correctpw123")
        self.assertIsNone(self.svc.authenticate(email="dora@example.com", password="nope"))
        self.assertIsNone(self.svc.authenticate(email="missing@example.com", password="whatever1"))

    def test_issued_jwt_round_trips(self) -> None:
        acct = self.svc.register(email="eve@example.com", password="password123")
        exp = int(time.time()) + 3600
        token = _make_jwt(acct.tenant_id, acct.user_id, list(acct.roles), exp, "test-secret")
        claims = verify_jwt(token, "test-secret")
        self.assertEqual(claims.tenant_id, acct.tenant_id)
        self.assertEqual(claims.user_id, acct.user_id)
        self.assertIn("admin", claims.roles)


if __name__ == "__main__":
    unittest.main()
