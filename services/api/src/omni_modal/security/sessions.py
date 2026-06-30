"""Auth session lifecycle: short-lived access tokens + rotating refresh tokens.

Phase 2 hardening. The previous flow minted a single 7-day JWT with no way to
revoke it if leaked. This module adds the production-standard pattern:

  * **Access token** — short-lived HS256 JWT (default 15 min, ``ACCESS_TOKEN_TTL_SECONDS``).
  * **Refresh token** — opaque, server-stored, long-lived (default 30 days,
    ``REFRESH_TOKEN_TTL_SECONDS``). Presented to ``/auth/refresh`` to mint a new
    access token. Rotated on every use (the old one is invalidated).
  * **Revocation** — refresh tokens live in a store, so ``/auth/logout`` (or an
    admin action) can revoke them immediately. Access tokens stay short-lived so
    a leaked one expires fast.
  * **Reuse detection** — if an already-rotated refresh token is presented
    again (a hallmark of token theft), the entire token *family* is revoked.

Only the SHA-256 hash of each refresh token is stored, never the token itself.

Storage is pluggable: Redis (shared across instances, survives restart) when
``REDIS_URL`` is set, else a thread-safe in-process store for local/demo. The
lifecycle logic is identical across both — only the key/value primitives differ.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from omni_modal.security.auth import _make_jwt, jwt_secret_from_env

_SESS_PREFIX = "omero:rt:sess:"
_USED_PREFIX = "omero:rt:used:"
_FAM_PREFIX = "omero:rt:fam:"

DEFAULT_ACCESS_TTL = 15 * 60          # 15 minutes
DEFAULT_REFRESH_TTL = 30 * 24 * 3600  # 30 days


class RefreshTokenError(Exception):
    """Raised when a refresh token is missing, expired, revoked, or reused."""


@dataclass(frozen=True)
class _AccountView:
    tenant_id: str
    user_id: str
    email: str
    roles: tuple[str, ...]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Storage primitives ────────────────────────────────────────────────────
class TokenKV(Protocol):
    def put_json(self, key: str, obj: dict, ttl: int) -> None: ...
    def get_json(self, key: str) -> dict | None: ...
    def delete(self, key: str) -> None: ...
    def add_to_set(self, key: str, member: str, ttl: int) -> None: ...
    def members(self, key: str) -> set[str]: ...


class InMemoryTokenKV:
    """Thread-safe, TTL-aware in-process KV for the offline/demo path."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._kv: dict[str, tuple[float, str]] = {}          # key -> (expires_at, json)
        self._sets: dict[str, dict[str, float]] = {}          # key -> {member: expires_at}

    def _expired(self, exp: float) -> bool:
        return exp != 0 and exp < time.time()

    def put_json(self, key: str, obj: dict, ttl: int) -> None:
        with self._lock:
            self._kv[key] = (time.time() + ttl if ttl else 0, json.dumps(obj))

    def get_json(self, key: str) -> dict | None:
        with self._lock:
            entry = self._kv.get(key)
            if entry is None:
                return None
            exp, raw = entry
            if self._expired(exp):
                self._kv.pop(key, None)
                return None
            return json.loads(raw)

    def delete(self, key: str) -> None:
        with self._lock:
            self._kv.pop(key, None)
            self._sets.pop(key, None)

    def add_to_set(self, key: str, member: str, ttl: int) -> None:
        with self._lock:
            members = self._sets.setdefault(key, {})
            members[member] = time.time() + ttl if ttl else 0

    def members(self, key: str) -> set[str]:
        with self._lock:
            members = self._sets.get(key, {})
            now = time.time()
            live = {m for m, exp in members.items() if exp == 0 or exp >= now}
            return live


class RedisTokenKV:
    """Redis-backed KV — shared across instances, survives restart."""

    def __init__(self, client) -> None:
        self._r = client

    def put_json(self, key: str, obj: dict, ttl: int) -> None:
        self._r.set(key, json.dumps(obj), ex=ttl)

    def get_json(self, key: str) -> dict | None:
        raw = self._r.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def add_to_set(self, key: str, member: str, ttl: int) -> None:
        pipe = self._r.pipeline()
        pipe.sadd(key, member)
        pipe.expire(key, ttl)
        pipe.execute()

    def members(self, key: str) -> set[str]:
        return set(self._r.smembers(key))


# ── Session service ────────────────────────────────────────────────────────
class SessionService:
    """Issues, rotates, and revokes access/refresh token pairs."""

    def __init__(
        self,
        kv: TokenKV | None = None,
        *,
        secret: str | None = None,
        access_ttl: int | None = None,
        refresh_ttl: int | None = None,
    ) -> None:
        self._kv = kv or InMemoryTokenKV()
        self._secret = secret
        self._access_ttl = access_ttl or int(
            os.environ.get("ACCESS_TOKEN_TTL_SECONDS", str(DEFAULT_ACCESS_TTL))
        )
        self._refresh_ttl = refresh_ttl or int(
            os.environ.get("REFRESH_TOKEN_TTL_SECONDS", str(DEFAULT_REFRESH_TTL))
        )

    # ── public API ─────────────────────────────────────────────────────
    def issue(self, account) -> dict:
        """Mint a fresh access + refresh pair for a newly-authenticated account."""
        view = _view(account)
        family_id = secrets.token_urlsafe(12)
        return self._build_response(view, family_id)

    def refresh(self, refresh_token: str) -> dict:
        """Validate + rotate a refresh token, returning a new access/refresh pair.

        Raises :class:`RefreshTokenError` if the token is invalid, expired,
        already-rotated (reuse → family revoked), or revoked.
        """
        if not refresh_token:
            raise RefreshTokenError("Refresh token is required.")
        h = _hash(refresh_token)

        reuse = self._kv.get_json(_USED_PREFIX + h)
        if reuse is not None:
            # An already-rotated token was replayed — assume theft, burn the family.
            self._revoke_family(reuse.get("family_id", ""))
            raise RefreshTokenError("Refresh token reuse detected; session revoked.")

        session = self._kv.get_json(_SESS_PREFIX + h)
        if session is None:
            raise RefreshTokenError("Invalid or expired refresh token.")

        # Rotate: invalidate the presented token, then mint a successor.
        self._kv.put_json(_USED_PREFIX + h, {"family_id": session["family_id"]}, self._refresh_ttl)
        self._kv.delete(_SESS_PREFIX + h)

        view = _AccountView(
            tenant_id=session["tenant_id"],
            user_id=session["user_id"],
            email=session.get("email", ""),
            roles=tuple(session.get("roles", [])),
        )
        return self._build_response(view, session["family_id"])

    def revoke(self, refresh_token: str) -> bool:
        """Revoke a single refresh token (logout). Returns True if one was active."""
        if not refresh_token:
            return False
        h = _hash(refresh_token)
        session = self._kv.get_json(_SESS_PREFIX + h)
        self._kv.delete(_SESS_PREFIX + h)
        if session is not None:
            self._kv.put_json(_USED_PREFIX + h, {"family_id": session["family_id"]}, self._refresh_ttl)
            return True
        return False

    # ── internals ──────────────────────────────────────────────────────
    def _build_response(self, view: _AccountView, family_id: str) -> dict:
        now = int(time.time())
        access_exp = now + self._access_ttl
        secret = self._secret or jwt_secret_from_env()
        access = _make_jwt(view.tenant_id, view.user_id, list(view.roles), access_exp, secret)

        refresh_token = secrets.token_urlsafe(32)
        refresh_exp = now + self._refresh_ttl
        self._kv.put_json(
            _SESS_PREFIX + _hash(refresh_token),
            {
                "tenant_id": view.tenant_id,
                "user_id": view.user_id,
                "email": view.email,
                "roles": list(view.roles),
                "family_id": family_id,
                "exp": refresh_exp,
            },
            self._refresh_ttl,
        )
        self._kv.add_to_set(_FAM_PREFIX + family_id, _hash(refresh_token), self._refresh_ttl)

        return {
            "token": access,
            "expires_at": access_exp,          # backward-compatible alias
            "access_expires_at": access_exp,
            "refresh_token": refresh_token,
            "refresh_expires_at": refresh_exp,
            "tenant_id": view.tenant_id,
            "user_id": view.user_id,
            "roles": list(view.roles),
            "email": view.email,
        }

    def _revoke_family(self, family_id: str) -> None:
        if not family_id:
            return
        fam_key = _FAM_PREFIX + family_id
        for member in self._kv.members(fam_key):
            self._kv.put_json(_USED_PREFIX + member, {"family_id": family_id}, self._refresh_ttl)
            self._kv.delete(_SESS_PREFIX + member)
        self._kv.delete(fam_key)


def _view(account) -> _AccountView:
    return _AccountView(
        tenant_id=account.tenant_id,
        user_id=account.user_id,
        email=getattr(account, "email", ""),
        roles=tuple(account.roles),
    )


# Process-wide in-memory KV so the offline/demo path keeps refresh tokens across
# requests within a single server process.
_in_memory_kv: InMemoryTokenKV | None = None
_kv_lock = threading.Lock()


def _shared_in_memory_kv() -> InMemoryTokenKV:
    global _in_memory_kv
    if _in_memory_kv is None:
        with _kv_lock:
            if _in_memory_kv is None:
                _in_memory_kv = InMemoryTokenKV()
    return _in_memory_kv


def select_session_service() -> SessionService:
    """Redis-backed session store when available, else in-process."""
    from omni_modal.cache.redis_client import get_redis_client  # noqa: PLC0415

    client = get_redis_client()
    if client is not None:
        return SessionService(RedisTokenKV(client))
    return SessionService(_shared_in_memory_kv())
