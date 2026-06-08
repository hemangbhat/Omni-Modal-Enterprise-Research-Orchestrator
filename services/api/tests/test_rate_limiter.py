"""Tests for the sliding window rate limiter (security/rate_limiting.py).

**Validates: Requirements 8.1, 8.2, 8.3, 8.6**
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import _path  # noqa: F401
import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.security.rate_limiting import (
    RateLimitConfig,
    RateLimitExceeded,
    SlidingWindowRateLimiter,
)


# ---------------------------------------------------------------------------
# 4.1  Property test — Property 15: Rate Limiter Allows Exactly `limit`
#      Requests Per Window
# ---------------------------------------------------------------------------

@given(
    limit=st.integers(min_value=1, max_value=30),
    overflow=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=100)
def test_rate_limiter_allows_exactly_limit_requests(limit: int, overflow: int) -> None:
    """Property 15: Rate Limiter Allows Exactly `limit` Requests Per Window
    Validates: Requirements 8.1, 8.2, 8.3, 8.6"""
    cfg = RateLimitConfig(tenant_rpm=limit, user_rpm=limit, delegation_rph=limit)
    limiter = SlidingWindowRateLimiter(cfg)

    # First `limit` requests must pass
    for i in range(limit):
        limiter.check_tenant("tenant1")  # should not raise

    # Next `overflow` requests must raise RateLimitExceeded
    for i in range(overflow):
        with pytest.raises(RateLimitExceeded) as exc_info:
            limiter.check_tenant("tenant1")
        assert exc_info.value.retry_after >= 1


# ---------------------------------------------------------------------------
# 4.2  Unit tests for SlidingWindowRateLimiter
# ---------------------------------------------------------------------------

class TestSlidingWindowRateLimiter(unittest.TestCase):

    # --- burst exactly at limit ---

    def test_burst_exactly_at_limit_passes(self) -> None:
        """Exactly `limit` requests within the window must all succeed."""
        cfg = RateLimitConfig(tenant_rpm=5, user_rpm=5, delegation_rph=5)
        limiter = SlidingWindowRateLimiter(cfg)
        for _ in range(5):
            limiter.check_tenant("t1")  # must not raise

    # --- burst exceeding limit raises ---

    def test_burst_exceeding_limit_raises(self) -> None:
        """The (limit+1)-th request must raise RateLimitExceeded."""
        cfg = RateLimitConfig(tenant_rpm=3, user_rpm=3, delegation_rph=3)
        limiter = SlidingWindowRateLimiter(cfg)
        for _ in range(3):
            limiter.check_tenant("t1")
        with self.assertRaises(RateLimitExceeded):
            limiter.check_tenant("t1")

    # --- window expiry: mock time to advance past the window ---

    def test_window_expiry_allows_new_requests(self) -> None:
        """After the sliding window has fully expired, requests should be allowed again."""
        cfg = RateLimitConfig(tenant_rpm=2, user_rpm=2, delegation_rph=2)
        limiter = SlidingWindowRateLimiter(cfg)
        start = 1000.0

        with patch("omni_modal.security.rate_limiting.time.monotonic") as mock_time:
            mock_time.return_value = start
            limiter.check_tenant("t1")
            limiter.check_tenant("t1")

            # Now at the limit — should raise
            with self.assertRaises(RateLimitExceeded):
                limiter.check_tenant("t1")

            # Advance time by 61 seconds — both timestamps are now outside the window
            mock_time.return_value = start + 61.0
            # Should succeed again
            limiter.check_tenant("t1")

    # --- Retry-After value is >= 1 ---

    def test_retry_after_is_at_least_one(self) -> None:
        """retry_after on RateLimitExceeded must be >= 1."""
        cfg = RateLimitConfig(tenant_rpm=1, user_rpm=1, delegation_rph=1)
        limiter = SlidingWindowRateLimiter(cfg)
        limiter.check_tenant("t1")
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.check_tenant("t1")
        self.assertGreaterEqual(ctx.exception.retry_after, 1)

    # --- different tenants have separate buckets ---

    def test_different_tenants_have_separate_buckets(self) -> None:
        """Requests for different tenants must not share rate limit state."""
        cfg = RateLimitConfig(tenant_rpm=2, user_rpm=2, delegation_rph=2)
        limiter = SlidingWindowRateLimiter(cfg)
        # Fill up tenant "a"
        limiter.check_tenant("a")
        limiter.check_tenant("a")
        with self.assertRaises(RateLimitExceeded):
            limiter.check_tenant("a")
        # tenant "b" must still be free
        limiter.check_tenant("b")  # must not raise
        limiter.check_tenant("b")  # must not raise

    # --- different users within the same tenant have separate buckets ---

    def test_different_users_have_separate_buckets(self) -> None:
        """check_user for different user IDs must use independent buckets."""
        cfg = RateLimitConfig(tenant_rpm=100, user_rpm=2, delegation_rph=100)
        limiter = SlidingWindowRateLimiter(cfg)
        # Fill up user "alice"
        limiter.check_user("tenant1", "alice")
        limiter.check_user("tenant1", "alice")
        with self.assertRaises(RateLimitExceeded):
            limiter.check_user("tenant1", "alice")
        # "bob" in same tenant must be unaffected
        limiter.check_user("tenant1", "bob")  # must not raise

    # --- check_delegation uses hourly window ---

    def test_check_delegation_uses_hourly_window(self) -> None:
        """check_delegation should enforce the delegation_rph limit over a 3600s window."""
        cfg = RateLimitConfig(tenant_rpm=100, user_rpm=100, delegation_rph=2)
        limiter = SlidingWindowRateLimiter(cfg)
        start = 5000.0

        with patch("omni_modal.security.rate_limiting.time.monotonic") as mock_time:
            mock_time.return_value = start
            limiter.check_delegation("tenant1")
            limiter.check_delegation("tenant1")

            # At the delegation limit — must raise
            with self.assertRaises(RateLimitExceeded) as ctx:
                limiter.check_delegation("tenant1")
            self.assertGreaterEqual(ctx.exception.retry_after, 1)

            # Advance 60 seconds (still within the hour window) — must still raise
            mock_time.return_value = start + 60.0
            with self.assertRaises(RateLimitExceeded):
                limiter.check_delegation("tenant1")

            # Advance past the full 3600s window — must pass again
            mock_time.return_value = start + 3601.0
            limiter.check_delegation("tenant1")  # must not raise

    # --- scope string is set correctly on the exception ---

    def test_rate_limit_exceeded_scope_tenant(self) -> None:
        """The scope on RateLimitExceeded should identify the tenant bucket."""
        cfg = RateLimitConfig(tenant_rpm=1, user_rpm=1, delegation_rph=1)
        limiter = SlidingWindowRateLimiter(cfg)
        limiter.check_tenant("myorg")
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.check_tenant("myorg")
        self.assertEqual(ctx.exception.scope, "t:myorg")

    def test_rate_limit_exceeded_scope_user(self) -> None:
        """The scope on RateLimitExceeded should identify the user bucket."""
        cfg = RateLimitConfig(tenant_rpm=100, user_rpm=1, delegation_rph=100)
        limiter = SlidingWindowRateLimiter(cfg)
        limiter.check_user("myorg", "alice")
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.check_user("myorg", "alice")
        self.assertEqual(ctx.exception.scope, "u:myorg:alice")

    def test_rate_limit_exceeded_scope_delegation(self) -> None:
        """The scope on RateLimitExceeded should identify the delegation bucket."""
        cfg = RateLimitConfig(tenant_rpm=100, user_rpm=100, delegation_rph=1)
        limiter = SlidingWindowRateLimiter(cfg)
        limiter.check_delegation("myorg")
        with self.assertRaises(RateLimitExceeded) as ctx:
            limiter.check_delegation("myorg")
        self.assertEqual(ctx.exception.scope, "d:myorg")


if __name__ == "__main__":
    unittest.main()
