"""Regression tests for the findings of the M0 whole-branch review.

Each class pins one finding: an enrolled user's MFA cannot be replaced without
turning it off first; wrong codes lock the second factor across login
attempts; long-lived sessions stay revocable; a configured database that
cannot be reached is reported instead of silently swapped for memory; and the
Postgres stores send the SQL the tables expect.
"""

from __future__ import annotations

import os
import time
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from unittest import mock

import _path  # noqa: F401

from omni_modal.security import mfa as mfa_module
from omni_modal.security import password_reset as reset_module
from omni_modal.security.mfa import (
    InMemoryMfaStore,
    MfaAlreadyEnrolled,
    MfaLocked,
    MfaService,
    PostgresMfaStore,
    totp,
)
from omni_modal.security.password_reset import PostgresResetStore
from omni_modal.security.sessions import InMemoryTokenKV, RefreshTokenError, SessionService


def _enrolled_service() -> tuple[MfaService, str]:
    svc = MfaService(InMemoryMfaStore())
    secret = svc.begin_enroll("t", "u", "a@b.c")["secret"]
    assert svc.confirm("t", "u", totp(secret, for_time=time.time() - 30))
    return svc, secret


class EnrolmentCannotBeOverwrittenTests(unittest.TestCase):
    def test_begin_enroll_is_refused_while_mfa_is_on(self) -> None:
        svc, secret = _enrolled_service()
        with self.assertRaises(MfaAlreadyEnrolled):
            svc.begin_enroll("t", "u", "a@b.c")
        self.assertTrue(svc.is_enrolled("t", "u"))
        self.assertTrue(svc.verify("t", "u", totp(secret)))  # original authenticator still works

    def test_unconfirmed_enrolment_can_be_restarted(self) -> None:
        svc = MfaService(InMemoryMfaStore())
        first = svc.begin_enroll("t", "u", "a@b.c")["secret"]
        second = svc.begin_enroll("t", "u", "a@b.c")["secret"]
        self.assertNotEqual(first, second)

    def test_enrolment_is_allowed_again_after_disable(self) -> None:
        svc, secret = _enrolled_service()
        self.assertTrue(svc.disable("t", "u", totp(secret)))
        self.assertIn("secret", svc.begin_enroll("t", "u", "a@b.c"))


class WrongCodeLockoutTests(unittest.TestCase):
    def test_five_wrong_codes_lock_the_second_factor(self) -> None:
        svc, secret = _enrolled_service()
        for _ in range(5):
            self.assertFalse(svc.verify("t", "u", "000000"))
        with self.assertRaises(MfaLocked) as caught:
            svc.verify("t", "u", totp(secret))  # even the right code is refused
        self.assertGreater(caught.exception.retry_after, 0)
        self.assertLessEqual(caught.exception.retry_after, 15 * 60)

    def test_lock_expires(self) -> None:
        svc, secret = _enrolled_service()
        for _ in range(5):
            svc.verify("t", "u", "000000")
        later = time.time() + 15 * 60 + 1
        with mock.patch.object(mfa_module.time, "time", return_value=later):
            self.assertTrue(svc.verify("t", "u", totp(secret, for_time=later)))

    def test_a_correct_code_resets_the_failure_count(self) -> None:
        svc, secret = _enrolled_service()
        for _ in range(4):
            self.assertFalse(svc.verify("t", "u", "000000"))
        self.assertTrue(svc.verify("t", "u", totp(secret)))
        for _ in range(4):
            self.assertFalse(svc.verify("t", "u", "000000"))
        # Four more failures after a success do not lock: the count restarted.
        self.assertTrue(svc.verify("t", "u", totp(secret, for_time=time.time() + 30)))

    def test_lock_also_blocks_disable(self) -> None:
        svc, secret = _enrolled_service()
        for _ in range(5):
            svc.verify("t", "u", "000000")
        with self.assertRaises(MfaLocked):
            svc.disable("t", "u", totp(secret))
        self.assertTrue(svc.is_enrolled("t", "u"))

    def test_non_ascii_digits_are_a_wrong_code_not_a_crash(self) -> None:
        svc, _ = _enrolled_service()
        self.assertFalse(svc.verify("t", "u", "１２３４５６"))  # fullwidth 123456


@dataclass(frozen=True)
class _Account:
    tenant_id: str = "t1"
    user_id: str = "u1"
    email: str = "a@b.com"
    roles: tuple[str, ...] = ("researcher",)


class LongLivedSessionRevocationTests(unittest.TestCase):
    def test_a_session_refreshed_past_the_original_ttl_is_still_revoked(self) -> None:
        svc = SessionService(InMemoryTokenKV(), secret="test-secret", access_ttl=900, refresh_ttl=3600)
        start = time.time()
        token = svc.issue(_Account())["refresh_token"]
        # Keep the session alive by refreshing before each token expires, until
        # well past the TTL of the index entry written at sign-in.
        for step in (1, 2, 3):
            with mock.patch("omni_modal.security.sessions.time.time", return_value=start + step * 2400):
                token = svc.refresh(token)["refresh_token"]
        with mock.patch("omni_modal.security.sessions.time.time", return_value=start + 3 * 2400 + 10):
            self.assertEqual(svc.revoke_user("t1", "u1"), 1)
            with self.assertRaises(RefreshTokenError):
                svc.refresh(token)


class ConfiguredDatabaseIsNotSilentlyIgnoredTests(unittest.TestCase):
    """With DATABASE_URL set but the pool unavailable, falling back to memory
    must be reported: otherwise auth state silently vanishes on restart."""

    def _select_with_broken_pool(self, select) -> list[str]:
        messages: list[str] = []

        def record(message, **_kwargs) -> None:
            messages.append(message)

        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgres://unused"}), \
             mock.patch("omni_modal.db.pool.get_connection_pool", side_effect=RuntimeError("psycopg_pool missing")), \
             mock.patch("omni_modal.observability.observability.capture_message", side_effect=record), \
             mock.patch("omni_modal.cache.redis_client.get_redis_client", return_value=None):
            select()
        return messages

    def test_mfa_store_fallback_is_reported(self) -> None:
        messages = self._select_with_broken_pool(mfa_module.select_mfa_service)
        self.assertEqual(len(messages), 1)
        self.assertIn("DATABASE_URL is set", messages[0])
        self.assertIn("psycopg_pool missing", messages[0])

    def test_reset_store_fallback_is_reported(self) -> None:
        messages = self._select_with_broken_pool(reset_module.select_password_reset_service)
        self.assertEqual(len(messages), 1)
        self.assertIn("DATABASE_URL is set", messages[0])

    def test_account_store_fallback_is_reported(self) -> None:
        from omni_modal.security import accounts

        messages = self._select_with_broken_pool(accounts.get_account_service)
        self.assertEqual(len(messages), 1)
        self.assertIn("DATABASE_URL is set", messages[0])


class _FakeCursor:
    def __init__(self, log: list, row) -> None:
        self._log = log
        self._row = row
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params=()) -> None:
        self._log.append((" ".join(sql.split()), tuple(params)))

    def fetchone(self):
        return self._row


class _FakePool:
    """Records every statement; returns `row` from fetchone()."""

    def __init__(self, row=None) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self._row = row

    @contextmanager
    def connection(self):
        pool = self

        class _Conn:
            def cursor(self):
                return _FakeCursor(pool.statements, pool._row)

        yield _Conn()


class PostgresMfaStoreSqlTests(unittest.TestCase):
    KEY = "omero:mfa:org-1:user-9"

    def test_get_maps_a_row_to_the_record(self) -> None:
        pool = _FakePool(row=("SECRET", True, 57, 2, 123.0))
        record = PostgresMfaStore(pool).get(self.KEY)
        self.assertEqual(record, {
            "secret": "SECRET", "confirmed": True, "last_counter": 57,
            "failed_attempts": 2, "locked_until": 123.0,
        })
        sql, params = pool.statements[0]
        self.assertIn("FROM user_mfa WHERE tenant_id = %s AND user_id = %s", sql)
        self.assertEqual(params, ("org-1", "user-9"))

    def test_get_returns_none_when_no_row(self) -> None:
        self.assertIsNone(PostgresMfaStore(_FakePool(row=None)).get(self.KEY))

    def test_put_upserts_every_column(self) -> None:
        pool = _FakePool()
        PostgresMfaStore(pool).put(self.KEY, {
            "secret": "SECRET", "confirmed": True, "last_counter": 57,
            "failed_attempts": 3, "locked_until": 99.5,
        })
        sql, params = pool.statements[0]
        self.assertIn("INSERT INTO user_mfa", sql)
        self.assertIn("ON CONFLICT (tenant_id, user_id) DO UPDATE", sql)
        self.assertEqual(params, ("org-1", "user-9", "SECRET", True, 57, 3, 99.5))

    def test_tenant_ids_containing_colons_are_split_on_the_last_one(self) -> None:
        pool = _FakePool(row=None)
        PostgresMfaStore(pool).get("omero:mfa:org:with:colons:user-9")
        self.assertEqual(pool.statements[0][1], ("org:with:colons", "user-9"))

    def test_delete_targets_one_user(self) -> None:
        pool = _FakePool()
        PostgresMfaStore(pool).delete(self.KEY)
        sql, params = pool.statements[0]
        self.assertIn("DELETE FROM user_mfa WHERE tenant_id = %s AND user_id = %s", sql)
        self.assertEqual(params, ("org-1", "user-9"))


class PostgresResetStoreSqlTests(unittest.TestCase):
    def test_add_stores_hash_email_and_relative_expiry(self) -> None:
        pool = _FakePool()
        PostgresResetStore(pool).add("hash-1", "user@example.com", 1800)
        sql, params = pool.statements[0]
        self.assertIn("INSERT INTO password_reset_tokens (token_hash, email, expires_at)", sql)
        self.assertEqual(params, ("hash-1", "user@example.com", 1800))

    def test_consume_is_a_single_guarded_update(self) -> None:
        pool = _FakePool(row=("user@example.com",))
        self.assertEqual(PostgresResetStore(pool).consume("hash-1"), "user@example.com")
        sql, params = pool.statements[0]
        self.assertEqual(len(pool.statements), 1)
        self.assertIn("UPDATE password_reset_tokens SET used_at = now()", sql)
        self.assertIn("used_at IS NULL AND expires_at > now()", sql)
        self.assertIn("RETURNING email", sql)
        self.assertEqual(params, ("hash-1",))

    def test_consume_returns_none_when_nothing_matched(self) -> None:
        self.assertIsNone(PostgresResetStore(_FakePool(row=None)).consume("hash-1"))

    def test_count_recent_is_case_insensitive_and_windowed(self) -> None:
        pool = _FakePool(row=(2,))
        self.assertEqual(PostgresResetStore(pool).count_recent("User@Example.com", 3600), 2)
        sql, params = pool.statements[0]
        self.assertIn("lower(email) = lower(%s)", sql)
        self.assertEqual(params, ("User@Example.com", 3600))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    os.environ.get("DATABASE_URL"),
    "DATABASE_URL not set — live Postgres store tests skipped (apply migration 0009 first).",
)
class LivePostgresAuthStoreTests(unittest.TestCase):
    """Runs the real SQL against the configured database. Each test uses a
    unique tenant/email and removes the rows it creates."""

    @classmethod
    def setUpClass(cls) -> None:
        from omni_modal.db.pool import get_connection_pool

        try:
            cls.pool = get_connection_pool()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Connection pool unavailable: {exc}") from exc

    def test_mfa_record_round_trip_and_lockout_columns(self) -> None:
        import uuid

        tenant = f"pgtest-{uuid.uuid4().hex[:8]}"
        svc = MfaService(PostgresMfaStore(self.pool))
        self.addCleanup(PostgresMfaStore(self.pool).delete, f"omero:mfa:{tenant}:u1")
        secret = svc.begin_enroll(tenant, "u1", "a@b.c")["secret"]
        self.assertFalse(svc.is_enrolled(tenant, "u1"))
        self.assertTrue(svc.confirm(tenant, "u1", totp(secret, for_time=time.time() - 30)))
        self.assertTrue(svc.is_enrolled(tenant, "u1"))
        for _ in range(5):
            self.assertFalse(svc.verify(tenant, "u1", "000000"))
        with self.assertRaises(MfaLocked):
            svc.verify(tenant, "u1", totp(secret))

    def test_reset_token_is_single_use_and_capped(self) -> None:
        import uuid

        from omni_modal.security.password_reset import PasswordResetService

        email = f"pgtest-{uuid.uuid4().hex[:8]}@example.com"

        def cleanup() -> None:
            with self.pool.connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM password_reset_tokens WHERE email = %s", (email,))

        self.addCleanup(cleanup)
        svc = PasswordResetService(PostgresResetStore(self.pool))
        token = svc.issue(email)
        self.assertEqual(svc.consume(token), email)
        self.assertIsNone(svc.consume(token))
        self.assertTrue(svc.issue(email))
        self.assertTrue(svc.issue(email))
        self.assertIsNone(svc.issue(email))  # fourth in the hour
