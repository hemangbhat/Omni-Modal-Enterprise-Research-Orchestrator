"""Time-based one-time password (TOTP) multi-factor auth — RFC 6238.

Zero external dependencies: TOTP is HMAC-based and implemented with the stdlib
(`hmac`/`hashlib`/`struct`). Works with any standard authenticator app (Google
Authenticator, Authy, 1Password) via the `otpauth://` provisioning URI.

Flow:
  1. `begin_enroll` issues a secret + `otpauth://` URI (render as a QR code).
  2. User scans it; `confirm` verifies a code and activates MFA.
  3. At login, if a user is enrolled, the password step returns an
     `mfa_required` challenge; `verify` completes the second factor.

Guards:
  * Each accepted code burns its time step, so a code cannot be replayed.
  * `MAX_FAILED_CODES` wrong codes in a row lock the second factor for
    `LOCKOUT_SECONDS`, across every login attempt, which bounds guessing by
    someone who already has the password.
  * An active second factor cannot be overwritten: it must be turned off
    (which needs a current code) before a new one can be enrolled.

The secret store is pluggable — Postgres (`user_mfa`) when `DATABASE_URL` is
set, Redis when only `REDIS_URL` is set, else an in-process store.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import struct
import threading
import time
from urllib.parse import quote

MAX_FAILED_CODES = 5
LOCKOUT_SECONDS = 15 * 60


class MfaAlreadyEnrolled(Exception):
    """Raised when enrollment is started for a user whose MFA is already on."""


class MfaLocked(Exception):
    """Raised while the second factor is locked after too many wrong codes."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Too many wrong codes. Try again in {retry_after} seconds.")
        self.retry_after = retry_after


# ── TOTP primitives (RFC 6238 / RFC 4226) ──────────────────────────────────
def generate_secret(num_bytes: int = 20) -> str:
    """Return a base32-encoded random secret (default 160-bit, RFC-recommended)."""
    return base64.b32encode(os.urandom(num_bytes)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = 6, algorithm: str = "sha1") -> str:
    # base32 decode (pad to a multiple of 8 chars).
    padding = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + padding)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, getattr(hashlib, algorithm)).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**digits)).zfill(digits)


def totp(secret_b32: str, for_time: float | None = None, step: int = 30, digits: int = 6,
         algorithm: str = "sha1") -> str:
    """Compute the TOTP code for a secret at a given time (now by default)."""
    if for_time is None:
        for_time = time.time()
    counter = int(for_time // step)
    return _hotp(secret_b32, counter, digits=digits, algorithm=algorithm)


def matching_counter(secret_b32: str, code: str, *, for_time: float | None = None,
                     step: int = 30, digits: int = 6, window: int = 1,
                     algorithm: str = "sha1") -> int | None:
    """Return the time-step counter a code matches (±`window` steps), else None.

    Callers that must reject replayed codes compare the result against the
    last counter they accepted.
    """
    code = (code or "").strip()
    # ASCII digits only: str.isdigit() alone also accepts other scripts'
    # digits, which cannot match and would break the constant-time compare.
    if len(code) != digits or not (code.isascii() and code.isdigit()):
        return None
    if for_time is None:
        for_time = time.time()
    counter = int(for_time // step)
    for drift in range(-window, window + 1):
        candidate = _hotp(secret_b32, counter + drift, digits=digits, algorithm=algorithm)
        if hmac.compare_digest(candidate, code):
            return counter + drift
    return None


def verify_totp(secret_b32: str, code: str, *, for_time: float | None = None,
                step: int = 30, digits: int = 6, window: int = 1,
                algorithm: str = "sha1") -> bool:
    """Constant-time verify a code, tolerating ±`window` steps for clock skew."""
    return matching_counter(
        secret_b32, code, for_time=for_time, step=step, digits=digits,
        window=window, algorithm=algorithm,
    ) is not None


def provisioning_uri(secret_b32: str, account_name: str, issuer: str = "OMERO") -> str:
    """Return an otpauth:// URI for QR enrollment in authenticator apps."""
    label = quote(f"{issuer}:{account_name}")
    params = f"secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    return f"otpauth://totp/{label}?{params}"


# ── Secret store + service ──────────────────────────────────────────────────
# A stored record is a dict with: secret, confirmed, last_counter (last accepted
# time step), failed_attempts (consecutive wrong codes) and locked_until (epoch
# seconds; 0 when not locked).
class InMemoryMfaStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        with self._lock:
            rec = self._data.get(key)
            return dict(rec) if rec else None

    def put(self, key: str, record: dict) -> None:
        with self._lock:
            self._data[key] = dict(record)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class RedisMfaStore:
    def __init__(self, client) -> None:
        self._r = client

    def get(self, key: str) -> dict | None:
        import json  # noqa: PLC0415

        raw = self._r.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None

    def put(self, key: str, record: dict) -> None:
        import json  # noqa: PLC0415

        self._r.set(key, json.dumps(record))

    def delete(self, key: str) -> None:
        self._r.delete(key)


class PostgresMfaStore:
    """Durable store in the ``user_mfa`` table (migration 0009), so enrollment
    survives restarts and is shared by every instance without Redis."""

    def __init__(self, pool) -> None:
        self._pool = pool

    @staticmethod
    def _split(key: str) -> tuple[str, str]:
        # Keys are "omero:mfa:{tenant_id}:{user_id}"; user ids never contain ":".
        tenant_id, user_id = key[len("omero:mfa:"):].rsplit(":", 1)
        return tenant_id, user_id

    def get(self, key: str) -> dict | None:
        tenant_id, user_id = self._split(key)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT secret, confirmed, last_counter, failed_attempts, locked_until
                   FROM user_mfa WHERE tenant_id = %s AND user_id = %s""",
                (tenant_id, user_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "secret": row[0], "confirmed": bool(row[1]), "last_counter": int(row[2]),
            "failed_attempts": int(row[3]), "locked_until": float(row[4]),
        }

    def put(self, key: str, record: dict) -> None:
        tenant_id, user_id = self._split(key)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO user_mfa
                       (tenant_id, user_id, secret, confirmed, last_counter,
                        failed_attempts, locked_until)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, user_id) DO UPDATE
                   SET secret = EXCLUDED.secret, confirmed = EXCLUDED.confirmed,
                       last_counter = EXCLUDED.last_counter,
                       failed_attempts = EXCLUDED.failed_attempts,
                       locked_until = EXCLUDED.locked_until, updated_at = now()""",
                (tenant_id, user_id, record["secret"], bool(record.get("confirmed")),
                 int(record.get("last_counter", -1)), int(record.get("failed_attempts", 0)),
                 float(record.get("locked_until", 0))),
            )

    def delete(self, key: str) -> None:
        tenant_id, user_id = self._split(key)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_mfa WHERE tenant_id = %s AND user_id = %s",
                (tenant_id, user_id),
            )


class MfaService:
    """Enroll, confirm, and verify TOTP second factors per (tenant, user)."""

    def __init__(self, store=None) -> None:
        self._store = store or InMemoryMfaStore()

    @staticmethod
    def _key(tenant_id: str, user_id: str) -> str:
        return f"omero:mfa:{tenant_id}:{user_id}"

    def begin_enroll(self, tenant_id: str, user_id: str, account_name: str) -> dict:
        """Create (or restart) a pending enrollment; returns secret + otpauth URI.

        Raises :class:`MfaAlreadyEnrolled` when MFA is already on: replacing an
        active second factor would switch it off without proof of possession.
        """
        existing = self._store.get(self._key(tenant_id, user_id))
        if existing and existing.get("confirmed"):
            raise MfaAlreadyEnrolled("Two-step sign-in is already on.")
        secret = generate_secret()
        self._store.put(
            self._key(tenant_id, user_id),
            {"secret": secret, "confirmed": False, "last_counter": -1,
             "failed_attempts": 0, "locked_until": 0},
        )
        return {"secret": secret, "otpauth_uri": provisioning_uri(secret, account_name)}

    def _accept(self, tenant_id: str, user_id: str, rec: dict, code: str) -> bool:
        """Accept a code once: a time step at or before the last accepted one
        is refused, so an observed code cannot be replayed inside its window."""
        counter = matching_counter(rec["secret"], code)
        if counter is None or counter <= int(rec.get("last_counter", -1)):
            return False
        rec["last_counter"] = counter
        rec["failed_attempts"] = 0
        rec["locked_until"] = 0
        self._store.put(self._key(tenant_id, user_id), rec)
        return True

    def confirm(self, tenant_id: str, user_id: str, code: str) -> bool:
        rec = self._store.get(self._key(tenant_id, user_id))
        if not rec:
            return False
        rec["confirmed"] = True  # persisted only if the code is accepted
        return self._accept(tenant_id, user_id, rec, code)

    def is_enrolled(self, tenant_id: str, user_id: str) -> bool:
        rec = self._store.get(self._key(tenant_id, user_id))
        return bool(rec and rec.get("confirmed"))

    def verify(self, tenant_id: str, user_id: str, code: str) -> bool:
        """Check a code for an enrolled user.

        Raises :class:`MfaLocked` while the factor is locked. The wrong code
        that reaches the limit returns False; the lock applies from the next
        call, including to correct codes.
        """
        rec = self._store.get(self._key(tenant_id, user_id))
        if not rec or not rec.get("confirmed"):
            return False
        now = time.time()
        locked_until = float(rec.get("locked_until", 0))
        if locked_until > now:
            raise MfaLocked(retry_after=max(1, math.ceil(locked_until - now)))
        if self._accept(tenant_id, user_id, rec, code):
            return True
        rec["failed_attempts"] = int(rec.get("failed_attempts", 0)) + 1
        if rec["failed_attempts"] >= MAX_FAILED_CODES:
            rec["failed_attempts"] = 0
            rec["locked_until"] = now + LOCKOUT_SECONDS
        self._store.put(self._key(tenant_id, user_id), rec)
        return False

    def disable(self, tenant_id: str, user_id: str, code: str) -> bool:
        """Remove MFA after verifying a current code (defense against hijack)."""
        if self.verify(tenant_id, user_id, code):
            self._store.delete(self._key(tenant_id, user_id))
            return True
        return False


_in_memory_store: InMemoryMfaStore | None = None
_store_lock = threading.Lock()


def _shared_store() -> InMemoryMfaStore:
    global _in_memory_store
    if _in_memory_store is None:
        with _store_lock:
            if _in_memory_store is None:
                _in_memory_store = InMemoryMfaStore()
    return _in_memory_store


def select_mfa_service() -> MfaService:
    """Postgres when configured (durable), else Redis (shared), else in-process."""
    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return MfaService(PostgresMfaStore(get_connection_pool()))
        except Exception as exc:  # noqa: BLE001 - fall through to a non-durable store
            from omni_modal.observability import observability  # noqa: PLC0415

            observability.capture_message(
                f"DATABASE_URL is set but the MFA store could not use Postgres ({exc}); "
                "two-step sign-in enrollment will NOT survive a restart.",
                operation="security.mfa.select", level="error",
            )
    from omni_modal.cache.redis_client import get_redis_client  # noqa: PLC0415

    client = get_redis_client()
    if client is not None:
        return MfaService(RedisMfaStore(client))
    return MfaService(_shared_store())
