"""Credential accounts (email + hashed password) for real login (Phase E).

``AccountService`` registers and authenticates users with PBKDF2-hashed
passwords. It is backed by the Postgres ``users`` table when ``DATABASE_URL``
is set (passwords in the ``password_hash`` column), and by a thread-safe
in-memory store otherwise — so the offline/demo path and tests work without a
database. New signups become the admin owner of a freshly provisioned tenant.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass

from omni_modal.security.passwords import hash_password, verify_password


class AccountError(Exception):
    """Raised for duplicate email or invalid registration input."""


@dataclass(frozen=True)
class Account:
    tenant_id: str
    user_id: str
    email: str
    roles: tuple[str, ...]


def _roles_for(role: str) -> tuple[str, ...]:
    # The org owner (admin) also gets researcher capabilities.
    return ("researcher", "admin") if role == "admin" else (role,)


class InMemoryAccountStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_email: dict[str, dict] = {}

    def create(self, *, tenant_id: str, email: str, display_name: str, role: str, password_hash: str) -> Account:
        key = email.lower()
        with self._lock:
            if key in self._by_email:
                raise AccountError("An account with this email already exists.")
            user_id = str(uuid.uuid4())
            self._by_email[key] = {
                "tenant_id": tenant_id, "user_id": user_id, "email": email,
                "role": role, "password_hash": password_hash,
            }
        return Account(tenant_id=tenant_id, user_id=user_id, email=email, roles=_roles_for(role))

    def find(self, email: str) -> dict | None:
        with self._lock:
            row = self._by_email.get(email.lower())
            return dict(row) if row else None


class PostgresAccountStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    def create(self, *, tenant_id: str, email: str, display_name: str, role: str, password_hash: str) -> Account:
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO users (tenant_id, email, display_name, role, password_hash)
                       VALUES (%s, %s, %s, %s, %s)
                       RETURNING id""",
                    (tenant_id, email, display_name, role, password_hash),
                )
                user_id = str(cur.fetchone()[0])
        except Exception as exc:
            # Unique violation (email already registered) or other integrity error.
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise AccountError("An account with this email already exists.") from exc
            raise
        return Account(tenant_id=tenant_id, user_id=user_id, email=email, roles=_roles_for(role))

    def find(self, email: str) -> dict | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, tenant_id, email, role, password_hash
                   FROM users
                   WHERE lower(email) = lower(%s) AND password_hash IS NOT NULL
                   LIMIT 1""",
                (email,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "user_id": str(row[0]), "tenant_id": row[1], "email": row[2],
            "role": row[3], "password_hash": row[4],
        }


class AccountService:
    def __init__(self, store=None) -> None:
        self._store = store or _select_store()

    def register(
        self, *, email: str, password: str,
        display_name: str | None = None, tenant_id: str | None = None,
    ) -> Account:
        email = (email or "").strip()
        if "@" not in email or len(email) > 320:
            raise AccountError("A valid email address is required.")
        if len(password or "") < 8:
            raise AccountError("Password must be at least 8 characters.")
        tenant = tenant_id or f"org-{uuid.uuid4().hex[:12]}"
        return self._store.create(
            tenant_id=tenant, email=email,
            display_name=display_name or email.split("@")[0],
            role="admin", password_hash=hash_password(password),
        )

    def authenticate(self, *, email: str, password: str) -> Account | None:
        row = self._store.find((email or "").strip())
        if not row or not verify_password(password, row.get("password_hash") or ""):
            return None
        return Account(
            tenant_id=row["tenant_id"], user_id=row["user_id"],
            email=row["email"], roles=_roles_for(row.get("role", "researcher")),
        )


def _select_store():
    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return PostgresAccountStore(get_connection_pool())
        except Exception:
            pass
    return InMemoryAccountStore()


def get_account_service() -> AccountService:
    return AccountService()
