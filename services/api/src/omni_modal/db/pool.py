from __future__ import annotations

import os
import threading
from omni_modal.observability import observability

try:
    from psycopg_pool import ConnectionPool  # type: ignore[import-not-found]
    _POOL_AVAILABLE = True
except ImportError:
    ConnectionPool = None  # type: ignore[assignment,misc]
    _POOL_AVAILABLE = False

_pool = None
_pool_lock = threading.Lock()


def get_connection_pool():
    """Lazy-initialise and return the module-level singleton pool.

    Reads DB_POOL_MIN (default 2) and DB_POOL_MAX (default 10) from env.
    Raises RuntimeError if DATABASE_URL is not set or psycopg_pool not installed.
    """
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is required.")
        if not _POOL_AVAILABLE:
            raise RuntimeError(
                "psycopg_pool is required for connection pooling. "
                "Install with: pip install 'omni-modal-api[performance]'"
            )
        min_size = int(os.environ.get("DB_POOL_MIN", "2"))
        max_size = int(os.environ.get("DB_POOL_MAX", "10"))
        _pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=5.0,
            reconnect_failed=_on_reconnect_failed,
            open=False,  # Don't open immediately; let caller decide
        )
    return _pool


def _on_reconnect_failed(pool) -> None:
    observability.capture_message(
        "ConnectionPool reconnect failed",
        operation="db.pool.reconnect_failed",
        level="error",
    )


def close_connection_pool() -> None:
    """Close the pool on shutdown (registered with atexit in main.py)."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None


def reset_pool_for_testing() -> None:
    """Reset the singleton for test isolation."""
    global _pool
    _pool = None
