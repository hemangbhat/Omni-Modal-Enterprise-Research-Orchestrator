from __future__ import annotations
import collections
import math
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    tenant_rpm: int = 60       # per-tenant requests per minute
    user_rpm: int = 20         # per-user requests per minute
    delegation_rph: int = 10   # per-tenant delegation requests per hour


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int, scope: str) -> None:
        super().__init__(f"Rate limit exceeded for {scope}.")
        self.retry_after = retry_after
        self.scope = scope


class SlidingWindowRateLimiter:
    """Token-based sliding window rate limiter (in-process, not distributed)."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._cfg = config or RateLimitConfig()
        self._windows: dict[str, collections.deque[float]] = {}

    def check_tenant(self, tenant_id: str) -> None:
        self._check(f"t:{tenant_id}", self._cfg.tenant_rpm, 60)

    def check_user(self, tenant_id: str, user_id: str) -> None:
        self._check(f"u:{tenant_id}:{user_id}", self._cfg.user_rpm, 60)

    def check_delegation(self, tenant_id: str) -> None:
        self._check(f"d:{tenant_id}", self._cfg.delegation_rph, 3600)

    def _check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        dq = self._windows.setdefault(key, collections.deque())

        # Evict expired timestamps
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) >= limit:
            oldest = dq[0]
            retry_after = math.ceil(oldest + window_seconds - now)
            raise RateLimitExceeded(retry_after=max(1, retry_after), scope=key)

        dq.append(now)
