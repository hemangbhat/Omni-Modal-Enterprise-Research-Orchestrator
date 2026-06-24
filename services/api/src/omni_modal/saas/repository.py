"""Postgres-backed persistence for the SaaS domain (Phase B).

These stores implement the exact same method surface as the in-memory
``WorkspaceStore`` and ``UsageStore`` so they are drop-in replacements. The
factory functions return the Postgres-backed store when ``DATABASE_URL`` is
configured and the connection pool / driver are available, and fall back to the
in-memory store otherwise — preserving the offline/demo path.

Ids are app-generated UUIDv4 strings and timestamps are epoch floats, matching
the dataclasses in ``workspaces.py`` exactly, so callers can't tell which
backend is active.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid

from omni_modal.observability import observability
from omni_modal.saas.usage import UsageStore, current_period
from omni_modal.saas.workspaces import (
    Invite,
    Membership,
    Organization,
    Workspace,
    WorkspaceStore,
    _slugify,
)


def _now() -> float:
    return time.time()


class PostgresWorkspaceStore:
    """Durable orgs/workspaces/members/invites backed by Postgres."""

    INVITE_TTL_SECONDS = 7 * 24 * 3600

    def __init__(self, pool) -> None:
        self._pool = pool

    # ── helpers ──────────────────────────────────────────────────────────
    def _conn(self):
        return self._pool.connection()

    # ── Organizations ────────────────────────────────────────────────────
    def create_org(
        self, *, tenant_id: str, name: str, owner_user_id: str, plan_id: str = "free"
    ) -> Organization:
        org = Organization(
            id=str(uuid.uuid4()), tenant_id=tenant_id, name=name, plan_id=plan_id,
            owner_user_id=owner_user_id, created_at=_now(),
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO organizations
                   (id, tenant_id, name, plan_id, owner_user_id, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id) DO NOTHING""",
                (org.id, tenant_id, name, plan_id, owner_user_id, org.created_at),
            )
        # If a row already existed for the tenant, return that one.
        existing = self.get_org_by_tenant(tenant_id)
        return existing or org

    def _row_to_org(self, row) -> Organization:
        return Organization(
            id=row[0], tenant_id=row[1], name=row[2], plan_id=row[3],
            owner_user_id=row[4], stripe_customer_id=row[5],
            stripe_subscription_id=row[6], created_at=float(row[7]),
        )

    _ORG_COLS = ("id, tenant_id, name, plan_id, owner_user_id, "
                 "stripe_customer_id, stripe_subscription_id, created_at")

    def get_org(self, org_id: str) -> Organization | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._ORG_COLS} FROM organizations WHERE id = %s", (org_id,))
            row = cur.fetchone()
        return self._row_to_org(row) if row else None

    def get_org_by_tenant(self, tenant_id: str) -> Organization | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._ORG_COLS} FROM organizations WHERE tenant_id = %s", (tenant_id,))
            row = cur.fetchone()
        return self._row_to_org(row) if row else None

    def get_org_by_stripe_customer(self, customer_id: str) -> Organization | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {self._ORG_COLS} FROM organizations WHERE stripe_customer_id = %s",
                (customer_id,),
            )
            row = cur.fetchone()
        return self._row_to_org(row) if row else None

    def set_plan(self, org_id: str, plan_id: str) -> Organization | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE organizations SET plan_id = %s WHERE id = %s", (plan_id, org_id))
        return self.get_org(org_id)

    def set_stripe_ids(
        self, org_id: str, *, customer_id: str | None = None, subscription_id: str | None = None
    ) -> Organization | None:
        sets, params = [], []
        if customer_id is not None:
            sets.append("stripe_customer_id = %s"); params.append(customer_id)
        if subscription_id is not None:
            sets.append("stripe_subscription_id = %s"); params.append(subscription_id)
        if sets:
            params.append(org_id)
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE organizations SET {', '.join(sets)} WHERE id = %s", params)
        return self.get_org(org_id)

    # ── Workspaces ───────────────────────────────────────────────────────
    def create_workspace(self, *, org_id: str, tenant_id: str, name: str) -> Workspace:
        ws = Workspace(
            id=str(uuid.uuid4()), org_id=org_id, tenant_id=tenant_id,
            name=name, slug=_slugify(name), created_at=_now(),
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO org_workspaces (id, org_id, tenant_id, name, slug, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (ws.id, org_id, tenant_id, name, ws.slug, ws.created_at),
            )
        return ws

    def _row_to_ws(self, row) -> Workspace:
        return Workspace(id=row[0], org_id=row[1], tenant_id=row[2], name=row[3],
                         slug=row[4], created_at=float(row[5]))

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, org_id, tenant_id, name, slug, created_at FROM org_workspaces WHERE id = %s",
                (workspace_id,),
            )
            row = cur.fetchone()
        return self._row_to_ws(row) if row else None

    def list_workspaces(self, org_id: str) -> list[Workspace]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, org_id, tenant_id, name, slug, created_at FROM org_workspaces "
                "WHERE org_id = %s ORDER BY created_at",
                (org_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_ws(r) for r in rows]

    def count_workspaces(self, org_id: str) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM org_workspaces WHERE org_id = %s", (org_id,))
            return int(cur.fetchone()[0])

    # ── Members ──────────────────────────────────────────────────────────
    def add_member(
        self, *, org_id: str, user_id: str, email: str, role: str, status: str = "active"
    ) -> Membership:
        member = Membership(org_id=org_id, user_id=user_id, email=email, role=role,
                            status=status, created_at=_now())
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO org_members (org_id, user_id, email, role, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (org_id, user_id)
                   DO UPDATE SET email = EXCLUDED.email, role = EXCLUDED.role,
                                 status = EXCLUDED.status""",
                (org_id, user_id, email, role, status, member.created_at),
            )
        return member

    def list_members(self, org_id: str) -> list[Membership]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT org_id, user_id, email, role, status, created_at FROM org_members "
                "WHERE org_id = %s ORDER BY created_at",
                (org_id,),
            )
            rows = cur.fetchall()
        return [
            Membership(org_id=r[0], user_id=r[1], email=r[2], role=r[3], status=r[4],
                       created_at=float(r[5]))
            for r in rows
        ]

    def count_members(self, org_id: str) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM org_members WHERE org_id = %s", (org_id,))
            return int(cur.fetchone()[0])

    def remove_member(self, org_id: str, user_id: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM org_members WHERE org_id = %s AND user_id = %s", (org_id, user_id))
            return cur.rowcount > 0

    # ── Invites ──────────────────────────────────────────────────────────
    def create_invite(self, *, org_id: str, email: str, role: str, invited_by: str) -> Invite:
        invite = Invite(
            id=str(uuid.uuid4()), org_id=org_id, email=email.strip().lower(), role=role,
            token=secrets.token_urlsafe(24), status="pending", created_at=_now(),
            expires_at=_now() + self.INVITE_TTL_SECONDS, invited_by=invited_by,
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO org_invites
                   (id, org_id, email, role, token, status, invited_by, created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (invite.id, org_id, invite.email, role, invite.token, invite.status,
                 invited_by, invite.created_at, invite.expires_at),
            )
        return invite

    def _row_to_invite(self, row) -> Invite:
        return Invite(id=row[0], org_id=row[1], email=row[2], role=row[3], token=row[4],
                      status=row[5], invited_by=row[6], created_at=float(row[7]),
                      expires_at=float(row[8]))

    _INV_COLS = "id, org_id, email, role, token, status, invited_by, created_at, expires_at"

    def get_invite(self, invite_id: str) -> Invite | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._INV_COLS} FROM org_invites WHERE id = %s", (invite_id,))
            row = cur.fetchone()
        return self._row_to_invite(row) if row else None

    def get_invite_by_token(self, token: str) -> Invite | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {self._INV_COLS} FROM org_invites WHERE token = %s", (token,))
            row = cur.fetchone()
        return self._row_to_invite(row) if row else None

    def list_invites(self, org_id: str) -> list[Invite]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {self._INV_COLS} FROM org_invites WHERE org_id = %s ORDER BY created_at",
                (org_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_invite(r) for r in rows]

    def accept_invite(self, token: str, *, user_id: str) -> Membership | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {self._INV_COLS} FROM org_invites WHERE token = %s FOR UPDATE",
                (token,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            invite = self._row_to_invite(row)
            if invite.status != "pending" or invite.is_expired:
                return None
            cur.execute("UPDATE org_invites SET status = 'accepted' WHERE id = %s", (invite.id,))
            created = _now()
            cur.execute(
                """INSERT INTO org_members (org_id, user_id, email, role, status, created_at)
                   VALUES (%s, %s, %s, %s, 'active', %s)
                   ON CONFLICT (org_id, user_id)
                   DO UPDATE SET email = EXCLUDED.email, role = EXCLUDED.role, status = 'active'""",
                (invite.org_id, user_id, invite.email, invite.role, created),
            )
        return Membership(org_id=invite.org_id, user_id=user_id, email=invite.email,
                          role=invite.role, status="active", created_at=created)

    def revoke_invite(self, invite_id: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE org_invites SET status = 'revoked' WHERE id = %s AND status = 'pending'",
                (invite_id,),
            )
            return cur.rowcount > 0


class PostgresUsageStore:
    """Durable monthly usage counters backed by Postgres."""

    def __init__(self, pool) -> None:
        self._pool = pool

    def record(self, tenant_id: str, metric: str, amount: int = 1, *, period: str | None = None) -> int:
        if amount < 0:
            raise ValueError("usage amount must be non-negative")
        period = period or current_period()
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO usage_counters (tenant_id, period, metric, count)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (tenant_id, period, metric)
                   DO UPDATE SET count = usage_counters.count + EXCLUDED.count
                   RETURNING count""",
                (tenant_id, period, metric, amount),
            )
            return int(cur.fetchone()[0])

    def get(self, tenant_id: str, metric: str, *, period: str | None = None) -> int:
        period = period or current_period()
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count FROM usage_counters WHERE tenant_id = %s AND period = %s AND metric = %s",
                (tenant_id, period, metric),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def snapshot(self, tenant_id: str, *, period: str | None = None) -> dict[str, int]:
        period = period or current_period()
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT metric, count FROM usage_counters WHERE tenant_id = %s AND period = %s",
                (tenant_id, period),
            )
            return {r[0]: int(r[1]) for r in cur.fetchall()}

    def all_tenants(self) -> list[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT tenant_id FROM usage_counters")
            return [r[0] for r in cur.fetchall()]


def _pool_or_none():
    """Return the connection pool if DATABASE_URL is set and the driver loads."""
    if not os.environ.get("DATABASE_URL"):
        return None
    try:
        from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

        return get_connection_pool()
    except Exception as exc:  # pragma: no cover - defensive
        observability.capture_message(
            f"Postgres pool unavailable ({exc}); using in-memory SaaS stores.",
            operation="saas.repository.pool",
            level="warning",
        )
        return None


def select_workspace_store() -> WorkspaceStore | PostgresWorkspaceStore:
    pool = _pool_or_none()
    if pool is None:
        return WorkspaceStore()
    return PostgresWorkspaceStore(pool)


def select_usage_store() -> UsageStore | PostgresUsageStore:
    pool = _pool_or_none()
    if pool is None:
        return UsageStore()
    return PostgresUsageStore(pool)


# ── Notifications ────────────────────────────────────────────────────────
from omni_modal.saas.notifications import Notification, NotificationStore  # noqa: E402


class PostgresNotificationStore:
    """Durable, tenant-scoped notifications backed by Postgres."""

    def __init__(self, pool) -> None:
        self._pool = pool

    def add(self, *, tenant_id: str, title: str, body: str = "", kind: str = "info",
            user_id: str | None = None) -> Notification:
        note = Notification(
            id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id, kind=kind,
            title=title, body=body, read=False, created_at=_now(),
        )
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO notifications
                   (id, tenant_id, user_id, kind, title, body, read, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, false, %s)""",
                (note.id, tenant_id, user_id, kind, title, body, note.created_at),
            )
        return note

    def _rows(self, tenant_id: str, user_id: str | None, unread_only: bool) -> list[Notification]:
        clauses = ["tenant_id = %s", "(user_id IS NULL OR %s::text IS NULL OR user_id = %s::text)"]
        params: list = [tenant_id, user_id, user_id]
        if unread_only:
            clauses.append("read = false")
        sql = (
            "SELECT id, tenant_id, user_id, kind, title, body, read, created_at "
            "FROM notifications WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC LIMIT 200"
        )
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            Notification(id=r[0], tenant_id=r[1], user_id=r[2], kind=r[3], title=r[4],
                         body=r[5], read=bool(r[6]), created_at=float(r[7]))
            for r in rows
        ]

    def list_for(self, tenant_id: str, *, user_id: str | None = None,
                 unread_only: bool = False) -> list[Notification]:
        return self._rows(tenant_id, user_id, unread_only)

    def unread_count(self, tenant_id: str, *, user_id: str | None = None) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM notifications WHERE tenant_id = %s "
                "AND (user_id IS NULL OR %s::text IS NULL OR user_id = %s::text) AND read = false",
                (tenant_id, user_id, user_id),
            )
            return int(cur.fetchone()[0])

    def mark_read(self, tenant_id: str, notification_id: str) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET read = true WHERE tenant_id = %s AND id = %s",
                (tenant_id, notification_id),
            )
            return cur.rowcount > 0

    def mark_all_read(self, tenant_id: str, *, user_id: str | None = None) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET read = true WHERE tenant_id = %s "
                "AND (user_id IS NULL OR %s::text IS NULL OR user_id = %s::text) AND read = false",
                (tenant_id, user_id, user_id),
            )
            return cur.rowcount


def select_notification_store() -> NotificationStore | PostgresNotificationStore:
    pool = _pool_or_none()
    if pool is None:
        return NotificationStore()
    return PostgresNotificationStore(pool)
