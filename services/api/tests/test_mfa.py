"""Unit tests for TOTP multi-factor auth (no HTTP, no database)."""

from __future__ import annotations

import base64
import time
import unittest

import _path  # noqa: F401

from omni_modal.security.mfa import (
    InMemoryMfaStore,
    MfaService,
    matching_counter,
    totp,
    verify_totp,
)

# RFC 6238 Appendix B test secret (ASCII "12345678901234567890").
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


class TotpPrimitiveTests(unittest.TestCase):
    def test_totp_matches_rfc6238_vector(self) -> None:
        self.assertEqual(totp(RFC_SECRET, for_time=59, digits=8), "94287082")

    def test_verify_accepts_adjacent_step_and_rejects_far_step(self) -> None:
        code = totp(RFC_SECRET, for_time=1000)
        self.assertTrue(verify_totp(RFC_SECRET, code, for_time=1030))
        self.assertFalse(verify_totp(RFC_SECRET, code, for_time=1090))

    def test_matching_counter_returns_the_step_that_matched(self) -> None:
        code = totp(RFC_SECRET, for_time=1000)
        self.assertEqual(matching_counter(RFC_SECRET, code, for_time=1030), 1000 // 30)
        self.assertIsNone(matching_counter(RFC_SECRET, "000000x", for_time=1000))

    def test_non_numeric_and_empty_codes_are_rejected(self) -> None:
        self.assertFalse(verify_totp(RFC_SECRET, "", for_time=1000))
        self.assertFalse(verify_totp(RFC_SECRET, "abcdef", for_time=1000))


class MfaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = MfaService(InMemoryMfaStore())

    def _enrol(self) -> str:
        """Enrol and confirm with the previous step's code, leaving the
        current and next steps unused for the test body."""
        secret = self.svc.begin_enroll("t", "u", "a@b.c")["secret"]
        self.assertTrue(self.svc.confirm("t", "u", totp(secret, for_time=time.time() - 30)))
        return secret

    def test_unconfirmed_enrolment_is_not_enrolled(self) -> None:
        self.svc.begin_enroll("t", "u", "a@b.c")
        self.assertFalse(self.svc.is_enrolled("t", "u"))

    def test_unconfirmed_enrolment_cannot_verify(self) -> None:
        secret = self.svc.begin_enroll("t", "u", "a@b.c")["secret"]
        self.assertFalse(self.svc.verify("t", "u", totp(secret)))

    def test_confirm_with_wrong_code_does_not_enrol(self) -> None:
        self.svc.begin_enroll("t", "u", "a@b.c")
        self.assertFalse(self.svc.confirm("t", "u", "000000"))
        self.assertFalse(self.svc.is_enrolled("t", "u"))

    def test_confirmed_user_verifies_with_current_code(self) -> None:
        secret = self._enrol()
        self.assertTrue(self.svc.is_enrolled("t", "u"))
        self.assertTrue(self.svc.verify("t", "u", totp(secret)))

    def test_same_code_cannot_be_used_twice(self) -> None:
        secret = self.svc.begin_enroll("t", "u", "a@b.c")["secret"]
        code = totp(secret)
        self.assertTrue(self.svc.confirm("t", "u", code))
        self.assertFalse(self.svc.verify("t", "u", code))  # confirm consumed this step

    def test_older_code_is_refused_after_a_newer_one_was_used(self) -> None:
        secret = self._enrol()
        now = time.time()
        self.assertTrue(self.svc.verify("t", "u", totp(secret, for_time=now + 30)))
        self.assertFalse(self.svc.verify("t", "u", totp(secret, for_time=now)))

    def test_disable_requires_valid_code(self) -> None:
        secret = self._enrol()
        self.assertFalse(self.svc.disable("t", "u", "000000"))
        self.assertTrue(self.svc.is_enrolled("t", "u"))
        self.assertTrue(self.svc.disable("t", "u", totp(secret)))
        self.assertFalse(self.svc.is_enrolled("t", "u"))

    def test_enrolment_is_isolated_per_tenant_and_user(self) -> None:
        self._enrol()
        self.assertFalse(self.svc.is_enrolled("other-tenant", "u"))
        self.assertFalse(self.svc.is_enrolled("t", "other-user"))


if __name__ == "__main__":
    unittest.main()
