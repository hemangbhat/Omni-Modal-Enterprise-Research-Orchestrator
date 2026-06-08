from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class JwtClaims:
    tenant_id: str
    user_id: str
    roles: tuple[str, ...]
    exp: int  # Unix timestamp


class AuthError(Exception):
    """Raised when a bearer token is absent, malformed, or invalid."""


def _b64url_decode(segment: str) -> bytes:
    padding = 4 - len(segment) % 4
    return base64.urlsafe_b64decode(segment + "=" * (padding % 4))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(tenant_id: str, user_id: str, roles: list[str], exp: int, secret: str) -> str:
    """Create a compact HS256 JWT for testing purposes."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "tenant_id": tenant_id,
        "user_id": user_id,
        "roles": roles,
        "exp": exp,
    }).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"


def verify_jwt(token: str, secret: str) -> JwtClaims:
    """Verify a compact HS256 JWT and return its claims.

    Raises AuthError for any structural or cryptographic failure.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("Malformed JWT: expected three dot-separated segments.")

    header_b64, payload_b64, sig_b64 = parts

    # Verify header
    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception as exc:
        raise AuthError(f"Malformed JWT header: {exc}") from exc
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise AuthError("JWT must use alg=HS256 and typ=JWT.")

    # Verify signature (constant-time)
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise AuthError(f"Malformed JWT signature: {exc}") from exc
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise AuthError("JWT signature verification failed.")

    # Decode payload
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:
        raise AuthError(f"Malformed JWT payload: {exc}") from exc

    # Validate required claims
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= int(time.time()):
        raise AuthError("JWT is expired or missing exp claim.")

    tenant_id = payload.get("tenant_id") or payload.get("tid")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise AuthError("JWT is missing tenant_id claim.")

    user_id = payload.get("user_id") or payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("JWT is missing user_id claim.")

    raw_roles = payload.get("roles", [])
    roles: tuple[str, ...] = tuple(r for r in raw_roles if isinstance(r, str))

    return JwtClaims(
        tenant_id=tenant_id, user_id=user_id, roles=roles, exp=exp
    )


def jwt_secret_from_env() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is not set.")
    return secret
