"""Tests for the Redis-backed distributed rate limiter (Phase 1 scaling).

Uses fakeredis so no live Redis server is required.
"""

from __future__ import annotations

import pytest

fakeredis = pytest.importorskip("fakeredis")

from omni_modal.security.rate_limiting import RateLimitConfig, RateLimitExceeded
from omni_modal.security.redis_rate_limiter import RedisRateLimiter, select_rate_limiter


@pytest.fixture()
def client():
    return fakeredis.FakeRedis(decode_responses=True)


def test_allows_requests_under_limit(client):
    limiter = RedisRateLimiter(client, RateLimitConfig(tenant_rpm=5))
    for _ in range(5):
        limiter.check_tenant("tenant-a")  # should not raise


def test_blocks_request_over_limit(client):
    limiter = RedisRateLimiter(client, RateLimitConfig(tenant_rpm=3))
    for _ in range(3):
        limiter.check_tenant("tenant-a")
    with pytest.raises(RateLimitExceeded) as exc:
        limiter.check_tenant("tenant-a")
    assert exc.value.retry_after >= 1


def test_tenants_are_isolated(client):
    limiter = RedisRateLimiter(client, RateLimitConfig(tenant_rpm=2))
    limiter.check_tenant("tenant-a")
    limiter.check_tenant("tenant-a")
    # A different tenant has its own window.
    limiter.check_tenant("tenant-b")
    limiter.check_tenant("tenant-b")
    with pytest.raises(RateLimitExceeded):
        limiter.check_tenant("tenant-a")


def test_user_and_tenant_windows_are_separate(client):
    limiter = RedisRateLimiter(client, RateLimitConfig(tenant_rpm=100, user_rpm=2))
    limiter.check_user("t1", "u1")
    limiter.check_user("t1", "u1")
    with pytest.raises(RateLimitExceeded):
        limiter.check_user("t1", "u1")
    # A different user under the same tenant is unaffected.
    limiter.check_user("t1", "u2")


def test_delegation_limit(client):
    limiter = RedisRateLimiter(client, RateLimitConfig(delegation_rph=1))
    limiter.check_delegation("t1")
    with pytest.raises(RateLimitExceeded):
        limiter.check_delegation("t1")


def test_rejected_request_does_not_consume_a_slot(client):
    """A blocked request must not leave a phantom entry that double-counts."""
    limiter = RedisRateLimiter(client, RateLimitConfig(tenant_rpm=2))
    limiter.check_tenant("t1")
    limiter.check_tenant("t1")
    for _ in range(3):
        with pytest.raises(RateLimitExceeded):
            limiter.check_tenant("t1")
    # The sorted set should still hold exactly the 2 successful entries.
    assert client.zcard("rl:t:t1") == 2


def test_fail_open_when_redis_errors():
    class BrokenClient:
        def pipeline(self):
            raise RuntimeError("redis down")

    limiter = RedisRateLimiter(BrokenClient(), RateLimitConfig(tenant_rpm=1))
    # Must not raise — availability is preferred when Redis is unreachable.
    limiter.check_tenant("t1")
    limiter.check_tenant("t1")


def test_select_rate_limiter_prefers_redis(client, monkeypatch):
    from omni_modal.cache import redis_client

    redis_client.set_test_client(client)
    try:
        limiter = select_rate_limiter()
        assert isinstance(limiter, RedisRateLimiter)
    finally:
        redis_client.set_test_client(None)


def test_select_rate_limiter_falls_back_in_memory(monkeypatch):
    from omni_modal.cache import redis_client
    from omni_modal.security.rate_limiting import SlidingWindowRateLimiter

    redis_client.set_test_client(None)
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_client.reset_for_testing()
    limiter = select_rate_limiter()
    assert isinstance(limiter, SlidingWindowRateLimiter)
