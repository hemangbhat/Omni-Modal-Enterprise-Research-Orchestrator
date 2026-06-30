"""Process-wide Redis client accessor.

The client is a lazily-initialised singleton built from the ``REDIS_URL``
environment variable. Upstash (and most managed Redis providers) hand out
``rediss://`` TLS URLs, which ``redis.Redis.from_url`` parses natively.

Design rules:
  * No hard dependency on ``redis`` — if the library is not installed the
    accessor returns ``None`` and callers fall back to their in-process path.
  * Connection failures never raise to the caller. A failed ``ping()`` returns
    ``None`` so the system stays up on the in-memory path instead of crashing.
  * ``set_test_client`` lets tests inject a ``fakeredis`` instance without a
    live server.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_client: Any = None
_test_client: Any = None
_lock = threading.Lock()
_warned = False


def set_test_client(client: Any) -> None:
    """Inject a client (e.g. ``fakeredis.FakeRedis``) for tests.

    Pass ``None`` to clear the injected client.
    """
    global _test_client, _client
    _test_client = client
    _client = None


def reset_for_testing() -> None:
    """Drop the cached real client so the next call re-reads the environment."""
    global _client, _warned
    _client = None
    _warned = False


def redis_configured() -> bool:
    """True when a Redis client is available (env-configured or injected)."""
    return _test_client is not None or bool(os.environ.get("REDIS_URL"))


def get_redis_client() -> Any:
    """Return the shared Redis client, or ``None`` when unavailable.

    Resolution order:
      1. A test client injected via :func:`set_test_client`.
      2. A singleton built from ``REDIS_URL`` (verified with ``ping()``).
      3. ``None`` — Redis is not configured or not reachable.
    """
    global _client, _warned

    if _test_client is not None:
        return _test_client
    if _client is not None:
        return _client

    url = os.environ.get("REDIS_URL")
    if not url:
        return None

    with _lock:
        if _client is not None:
            return _client
        try:
            import redis  # type: ignore[import-not-found]
        except ImportError:
            if not _warned:
                logger.warning(
                    "REDIS_URL is set but the 'redis' package is not installed; "
                    "falling back to in-process rate limiting/cache/queue. "
                    "Install with: pip install -e '.[scale]'"
                )
                _warned = True
            return None

        try:
            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            client.ping()
        except Exception as exc:  # noqa: BLE001 - degrade, never crash
            if not _warned:
                logger.warning(
                    "Redis at REDIS_URL is unreachable (%s); falling back to "
                    "in-process implementations.",
                    exc,
                )
                _warned = True
            return None

        _client = client
        logger.info("Connected to Redis; distributed rate limiter/cache/queue active.")
        return _client
