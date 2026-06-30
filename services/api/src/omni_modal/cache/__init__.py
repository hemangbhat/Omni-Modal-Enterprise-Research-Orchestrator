"""Shared Redis-backed coordination primitives (Phase 1 — horizontal scaling).

This package holds the process-wide Redis client accessor used by the
distributed rate limiter, the shared query cache, and the durable ingestion
queue. Every consumer degrades gracefully to an in-process implementation when
``REDIS_URL`` is unset or Redis is unreachable, so the offline/demo path keeps
working with zero configuration.
"""

from omni_modal.cache.redis_client import (
    get_redis_client,
    redis_configured,
    reset_for_testing,
    set_test_client,
)

__all__ = [
    "get_redis_client",
    "redis_configured",
    "reset_for_testing",
    "set_test_client",
]
