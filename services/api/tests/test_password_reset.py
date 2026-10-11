"""Unit tests for password-reset tokens and the account/session operations a
reset relies on (no HTTP, no database)."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

import _path  # noqa: F401

from omni_modal.security.accounts import AccountError, AccountService, InMemoryAccountStore
from omni_modal.security.password_reset import InMemoryResetStore, PasswordResetService
from omni_modal.security.sessions import InMemoryTokenKV, RefreshTokenError, SessionService


class PasswordResetServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResetStore()
        self.svc = PasswordResetService(self.store)

    def test_token_consumed_once(self) -> None:
        token = self.svc.issue("user@example.com")
        self.assertTrue(token)
        self.assertEqual(self.svc.consume(token), "user@example.com")
        self.assertIsNone(self.svc.consume(token))

    def test_unknown_and_empty_tokens_are_refused(self) -> None:
        self.assertIsNone(self.svc.consume("not-a-real-token"))
        self.assertIsNone(self.svc.consume(""))

    def test_expired_token_refused(self) -> None:
        svc = PasswordResetService(self.store, ttl_seconds=0)
        token = svc.issue("user@example.com")
        self.assertIsNone(svc.consume(token))

    def test_raw_token_not_stored(self) -> None:
        token = self.svc.issue("user@example.com")
        self.assertNotIn(token, repr(self.store.dump()))

    def test_hourly_cap(self) -> None:
        for _ in range(3):
            self.assertTrue(self.svc.issue("user@example.com"))
        self.assertIsNone(self.svc.issue("user@example.com"))
        # The cap is per address.
        self.assertTrue(self.svc.issue("other@example.com"))

    def test_email_match_is_case_insensitive(self) -> None:
        for _ in range(3):
            self.svc.issue("User@Example.com")
        self.assertIsNone(self.svc.issue("user@example.COM"))

    def test_each_token_is_distinct_and_independently_usable(self) -> None:
        first = self.svc.issue("user@example.com")
        second = self.svc.issue("user@example.com")
        self.assertNotEqual(first, second)
        self.assertEqual(self.svc.consume(second), "user@example.com")
        self.assertEqual(self.svc.consume(first), "user@example.com")


class AccountSetPasswordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.accounts = AccountService(InMemoryAccountStore())
        self.accounts.register(email="user@example.com", password="old-password")

    def test_set_password_replaces_the_credential(self) -> None:
        account = self.accounts.set_password("USER@example.com", "new-password")
        self.assertIsNotNone(account)
        self.assertEqual(account.email, "user@example.com")
        self.assertIsNone(self.accounts.authenticate(email="user@example.com", password="old-password"))
        self.assertIsNotNone(self.accounts.authenticate(email="user@example.com", password="new-password"))

    def test_set_password_rejects_short_password(self) -> None:
        with self.assertRaises(AccountError):
            self.accounts.set_password("user@example.com", "short")
        self.assertIsNotNone(self.accounts.authenticate(email="user@example.com", password="old-password"))

    def test_set_password_for_unknown_email_returns_none(self) -> None:
        self.assertIsNone(self.accounts.set_password("nobody@example.com", "new-password"))

    def test_find_account_returns_identity_without_credentials(self) -> None:
        account = self.accounts.find_account("user@example.com")
        self.assertEqual(account.email, "user@example.com")
        self.assertIsNone(self.accounts.find_account("nobody@example.com"))


@dataclass(frozen=True)
class _Account:
    tenant_id: str = "t1"
    user_id: str = "u1"
    email: str = "a@b.com"
    roles: tuple[str, ...] = ("researcher",)


class RevokeUserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = SessionService(InMemoryTokenKV(), secret="test-secret", access_ttl=900, refresh_ttl=3600)

    def test_revoke_user_invalidates_every_session_of_that_user(self) -> None:
        laptop = self.svc.issue(_Account())["refresh_token"]
        phone = self.svc.refresh(self.svc.issue(_Account())["refresh_token"])["refresh_token"]
        self.assertEqual(self.svc.revoke_user("t1", "u1"), 2)
        for token in (laptop, phone):
            with self.assertRaises(RefreshTokenError):
                self.svc.refresh(token)

    def test_revoke_user_leaves_other_users_signed_in(self) -> None:
        other = self.svc.issue(_Account(user_id="u2"))["refresh_token"]
        same_id_other_tenant = self.svc.issue(_Account(tenant_id="t2"))["refresh_token"]
        self.svc.issue(_Account())
        self.svc.revoke_user("t1", "u1")
        self.assertTrue(self.svc.refresh(other)["token"])
        self.assertTrue(self.svc.refresh(same_id_other_tenant)["token"])

    def test_revoke_user_with_no_sessions_returns_zero(self) -> None:
        self.assertEqual(self.svc.revoke_user("t1", "nobody"), 0)


if __name__ == "__main__":
    unittest.main()
