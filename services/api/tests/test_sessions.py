"""Tests for the access/refresh token lifecycle (Phase 2 auth hardening)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from omni_modal.security.auth import verify_jwt
from omni_modal.security.sessions import (
    InMemoryTokenKV,
    RedisTokenKV,
    RefreshTokenError,
    SessionService,
)

SECRET = "test-secret-key"


@dataclass(frozen=True)
class _Account:
    tenant_id: str = "t1"
    user_id: str = "u1"
    email: str = "a@b.com"
    roles: tuple[str, ...] = ("researcher", "admin")


def _service(kv=None) -> SessionService:
    return SessionService(kv or InMemoryTokenKV(), secret=SECRET, access_ttl=900, refresh_ttl=3600)


def test_issue_returns_access_and_refresh():
    svc = _service()
    result = svc.issue(_Account())
    assert result["token"]
    assert result["refresh_token"]
    assert result["access_expires_at"] == result["expires_at"]
    assert result["refresh_expires_at"] > result["access_expires_at"]
    # Access token is a valid JWT carrying the account identity.
    claims = verify_jwt(result["token"], SECRET)
    assert claims.tenant_id == "t1"
    assert claims.user_id == "u1"
    assert set(claims.roles) == {"researcher", "admin"}


def test_refresh_rotates_token():
    svc = _service()
    issued = svc.issue(_Account())
    rotated = svc.refresh(issued["refresh_token"])
    # New refresh token is different (rotation).
    assert rotated["refresh_token"] != issued["refresh_token"]
    # New access token is valid.
    assert verify_jwt(rotated["token"], SECRET).user_id == "u1"


def test_old_refresh_token_is_invalid_after_rotation():
    svc = _service()
    issued = svc.issue(_Account())
    svc.refresh(issued["refresh_token"])
    # Re-presenting the original (now-rotated) token is reuse → rejected.
    with pytest.raises(RefreshTokenError):
        svc.refresh(issued["refresh_token"])


def test_reuse_detection_revokes_whole_family():
    svc = _service()
    issued = svc.issue(_Account())
    rotated = svc.refresh(issued["refresh_token"])
    # Replaying the old token triggers family revocation.
    with pytest.raises(RefreshTokenError):
        svc.refresh(issued["refresh_token"])
    # The legitimately-rotated successor is now also dead.
    with pytest.raises(RefreshTokenError):
        svc.refresh(rotated["refresh_token"])


def test_invalid_refresh_token_rejected():
    svc = _service()
    with pytest.raises(RefreshTokenError):
        svc.refresh("not-a-real-token")


def test_empty_refresh_token_rejected():
    svc = _service()
    with pytest.raises(RefreshTokenError):
        svc.refresh("")


def test_revoke_logout_invalidates_refresh():
    svc = _service()
    issued = svc.issue(_Account())
    assert svc.revoke(issued["refresh_token"]) is True
    with pytest.raises(RefreshTokenError):
        svc.refresh(issued["refresh_token"])


def test_revoke_unknown_token_returns_false():
    svc = _service()
    assert svc.revoke("unknown") is False


def test_multiple_sessions_are_independent():
    """Two logins (two families) — revoking one must not affect the other."""
    svc = _service()
    s1 = svc.issue(_Account())
    s2 = svc.issue(_Account())
    svc.revoke(s1["refresh_token"])
    # s2 still refreshes fine.
    assert svc.refresh(s2["refresh_token"])["token"]


# ── Redis-backed parity (fakeredis) ────────────────────────────────────────
def test_redis_backed_lifecycle():
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeRedis(decode_responses=True)
    svc = SessionService(RedisTokenKV(client), secret=SECRET, access_ttl=900, refresh_ttl=3600)
    issued = svc.issue(_Account())
    rotated = svc.refresh(issued["refresh_token"])
    assert rotated["refresh_token"] != issued["refresh_token"]
    with pytest.raises(RefreshTokenError):
        svc.refresh(issued["refresh_token"])
    # Revocation works against a fresh session (the one above was family-burned).
    fresh = svc.issue(_Account())
    assert svc.revoke(fresh["refresh_token"]) is True


def test_select_session_service_prefers_redis():
    fakeredis = pytest.importorskip("fakeredis")
    from omni_modal.cache import redis_client
    from omni_modal.security.sessions import RedisTokenKV, select_session_service

    redis_client.set_test_client(fakeredis.FakeRedis(decode_responses=True))
    try:
        svc = select_session_service()
        assert isinstance(svc._kv, RedisTokenKV)
    finally:
        redis_client.set_test_client(None)
