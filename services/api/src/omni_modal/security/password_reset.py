"""Password-reset tokens: single-use, expiring, stored only as a hash.

``PasswordResetService.issue`` mints a random token for an email address and
``consume`` exchanges it — once — for that address. Only the SHA-256 of the
token is stored, so a leaked table cannot be used to reset passwords; the raw
token exists only in the email sent to the user.

Backed by the Postgres ``password_reset_tokens`` table (migration 0009) when
``DATABASE_URL`` is set, so a link still works after a restart, and by a
thread-safe in-memory store otherwise.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time

DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_MAX_PER_HOUR = 3


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InMemoryResetStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # token_hash -> {"email", "expires_at", "created_at", "used"}
        self._rows: dict[str, dict] = {}

    def add(self, token_hash: str, email: str, ttl_seconds: int) -> None:
        now = time.time()
        with self._lock:
            self._rows[token_hash] = {
                "email": email, "expires_at": now + ttl_seconds, "created_at": now, "used": False,
            }

    def count_recent(self, email: str, window_seconds: int) -> int:
        cutoff = time.time() - window_seconds
        with self._lock:
            return sum(
                1 for row in self._rows.values()
                if row["email"].lower() == email.lower() and row["created_at"] >= cutoff
            )

    def consume(self, token_hash: str) -> str | None:
        with self._lock:
            row = self._rows.get(token_hash)
            if row is None or row["used"] or row["expires_at"] <= time.time():
                return None
            row["used"] = True
            return row["email"]

    def dump(self) -> dict[str, dict]:
        """Snapshot of stored rows (for tests asserting what is persisted)."""
        with self._lock:
            return {key: dict(row) for key, row in self._rows.items()}


class PostgresResetStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    def add(self, token_hash: str, email: str, ttl_seconds: int) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO password_reset_tokens (token_hash, email, expires_at)
                   VALUES (%s, %s, now() + make_interval(secs => %s))""",
                (token_hash, email, ttl_seconds),
            )

    def count_recent(self, email: str, window_seconds: int) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM password_reset_tokens
                   WHERE lower(email) = lower(%s)
                     AND created_at >= now() - make_interval(secs => %s)""",
                (email, window_seconds),
            )
            return int(cur.fetchone()[0])

    def consume(self, token_hash: str) -> str | None:
        # One atomic statement, so two concurrent uses of a link cannot both win.
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE password_reset_tokens SET used_at = now()
                   WHERE token_hash = %s AND used_at IS NULL AND expires_at > now()
                   RETURNING email""",
                (token_hash,),
            )
            row = cur.fetchone()
        return row[0] if row else None


class PasswordResetService:
    def __init__(
        self, store=None, *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_per_hour: int = DEFAULT_MAX_PER_HOUR,
    ) -> None:
        self._store = store or InMemoryResetStore()
        self._ttl = ttl_seconds
        self._max_per_hour = max_per_hour

    def issue(self, email: str) -> str | None:
        """Mint a reset token for ``email``. Returns None once the address has
        reached its hourly cap, which bounds reset-email flooding."""
        if self._store.count_recent(email, 3600) >= self._max_per_hour:
            return None
        token = secrets.token_urlsafe(32)
        self._store.add(_hash(token), email, self._ttl)
        return token

    def consume(self, token: str) -> str | None:
        """Exchange a token for its email address. Works once, before expiry."""
        if not token:
            return None
        return self._store.consume(_hash(token))


_in_memory_store: InMemoryResetStore | None = None
_store_lock = threading.Lock()


def _shared_store() -> InMemoryResetStore:
    global _in_memory_store
    if _in_memory_store is None:
        with _store_lock:
            if _in_memory_store is None:
                _in_memory_store = InMemoryResetStore()
    return _in_memory_store


def select_password_reset_service() -> PasswordResetService:
    """Postgres-backed when configured (links survive restarts), else in-process."""
    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return PasswordResetService(PostgresResetStore(get_connection_pool()))
        except Exception:  # noqa: BLE001 - fall back to the in-process store
            pass
    return PasswordResetService(_shared_store())
