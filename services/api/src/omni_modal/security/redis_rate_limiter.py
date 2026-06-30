"""Distributed sliding-window rate limiter backed by Redis sorted sets.

This mirrors the exact public surface of
:class:`omni_modal.security.rate_limiting.SlidingWindowRateLimiter`
(``check_tenant`` / ``check_user`` / ``check_delegation``) so it is a drop-in
replacement. The difference is that the request counters live in Redis, so
*every* web instance shares one source of truth — the prerequisite for a
stateless, horizontally-scalable web tier.

Algorithm (per key, per window):
  1. ``ZREMRANGEBYSCORE`` evicts timestamps older than the window.
  2. ``ZCARD`` reads the current count *before* this request.
  3. ``ZADD`` records this request; ``EXPIRE`` keeps the key self-cleaning.
Steps 1–4 run in a single pipeline/transaction. If the pre-add count already
meets the limit, the just-added member is removed and ``RateLimitExceeded`` is
raised with a ``Retry-After`` derived from the oldest timestamp in the window.

Fail-open: if Redis errors mid-check the limiter allows the request rather than
taking the whole API down. Availability is preferred over strict enforcement
for this control; abuse is still bounded by the in-process limiter when Redis
is entirely absent.
"""

from __future__ import annotations

import logging
import math
import time
from uuid import uuid4

from omni_modal.security.rate_limiting import (
    RateLimitConfig,
    RateLimitExceeded,
    SlidingWindowRateLimiter,
)

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Sliding-window rate limiter sharing state across instances via Redis."""

    def __init__(self, client, config: RateLimitConfig | None = None, *, namespace: str = "rl") -> None:
        self._r = client
        self._cfg = config or RateLimitConfig()
        self._ns = namespace

    def check_tenant(self, tenant_id: str) -> None:
        self._check(f"{self._ns}:t:{tenant_id}", self._cfg.tenant_rpm, 60)

    def check_user(self, tenant_id: str, user_id: str) -> None:
        self._check(f"{self._ns}:u:{tenant_id}:{user_id}", self._cfg.user_rpm, 60)

    def check_delegation(self, tenant_id: str) -> None:
        self._check(f"{self._ns}:d:{tenant_id}", self._cfg.delegation_rph, 3600)

    def _check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.time()
        cutoff = now - window_seconds
        member = f"{now:.6f}:{uuid4().hex}"

        try:
            pipe = self._r.pipeline()
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            pipe.zadd(key, {member: now})
            pipe.expire(key, window_seconds + 1)
            results = pipe.execute()
            count_before = int(results[1])
        except Exception as exc:  # noqa: BLE001 - fail open on Redis errors
            logger.warning("RedisRateLimiter degraded (allowing request): %s", exc)
            return

        if count_before >= limit:
            # Roll back the optimistic add for this rejected request.
            try:
                self._r.zrem(key, member)
            except Exception:  # noqa: BLE001
                pass
            retry_after = window_seconds
            try:
                oldest = self._r.zrange(key, 0, 0, withscores=True)
                if oldest:
                    oldest_score = float(oldest[0][1])
                    retry_after = math.ceil(oldest_score + window_seconds - now)
            except Exception:  # noqa: BLE001
                pass
            raise RateLimitExceeded(retry_after=max(1, int(retry_after)), scope=key)


def select_rate_limiter(config: RateLimitConfig | None = None):
    """Return a Redis-backed limiter when Redis is available, else in-process.

    Keeping the selection here means callers (`main.py`, the FastAPI app) do not
    need to know which implementation is active — both expose the same methods.
    """
    from omni_modal.cache.redis_client import get_redis_client  # noqa: PLC0415

    client = get_redis_client()
    if client is not None:
        logger.info("Rate limiting: Redis-backed (distributed).")
        return RedisRateLimiter(client, config)
    return SlidingWindowRateLimiter(config)
