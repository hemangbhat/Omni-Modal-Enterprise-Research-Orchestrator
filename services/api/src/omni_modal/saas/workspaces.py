"""Organizations, workspaces, members, and invites.

Data model
----------
- ``Organization`` is the billing/tenant boundary. It maps 1:1 to the existing
  JWT ``tenant_id`` so the SaaS layer sits on top of the current auth model
  without breaking it.
- ``Workspace`` is a sub-container inside an org (e.g. "Oncology Research",
  "Clinical Trials"). Documents/usage are scoped to a workspace.
- ``Membership`` ties a user to an org with a role.
- ``Invite`` is a pending membership keyed by a random token. Sending the
  invite email is delegated to an optional email adapter; the invite record
  itself is always created locally so the flow works offline.

The store is in-memory and thread-safe (guarded by a lock) because the HTTP
handler and background worker run in separate threads.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field

from omni_modal.saas.plans import DEFAULT_PLAN_ID


def _now() -> float:
    return time.time()


def _slugify(name: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in name.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "workspace"


@dataclass
class Organization:
    id: str
    tenant_id: str
    name: str
    plan_id: str
    owner_user_id: str
    created_at: float
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "plan_id": self.plan_id,
            "owner_user_id": self.owner_user_id,
            "created_at": self.created_at,
            "stripe_customer_id": self.stripe_customer_id,
            "stripe_subscription_id": self.stripe_subscription_id,
        }


@dataclass
class Workspace:
    id: str
    org_id: str
    tenant_id: str
    name: str
    slug: str
    created_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "slug": self.slug,
            "created_at": self.created_at,
        }


@dataclass
class Membership:
    org_id: str
    user_id: str
    email: str
    role: str
    status: str  # "active" | "invited"
    created_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "org_id": self.org_id,
            "user_id": self.user_id,
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at,
        }


@dataclass
class Invite:
    id: str
    org_id: str
    email: str
    role: str
    token: str
    status: str  # "pending" | "accepted" | "revoked"
    created_at: float
    expires_at: float
    invited_by: str

    @property
    def is_expired(self) -> bool:
        return _now() > self.expires_at

    def to_dict(self, *, include_token: bool = False) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "org_id": self.org_id,
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "invited_by": self.invited_by,
            "expired": self.is_expired,
        }
        if include_token:
            data["token"] = self.token
        return data


class WorkspaceStore:
    """Thread-safe in-memory store for orgs, workspaces, members, and invites."""

    INVITE_TTL_SECONDS = 7 * 24 * 3600

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._orgs: dict[str, Organization] = {}
        self._orgs_by_tenant: dict[str, str] = {}
        self._workspaces: dict[str, Workspace] = {}
        self._memberships: dict[str, list[Membership]] = {}  # org_id -> members
        self._invites: dict[str, Invite] = {}  # invite_id -> invite

    # ── Organizations ────────────────────────────────────────────────────
    def create_org(
        self, *, tenant_id: str, name: str, owner_user_id: str, plan_id: str = DEFAULT_PLAN_ID
    ) -> Organization:
        with self._lock:
            org = Organization(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                name=name,
                plan_id=plan_id,
                owner_user_id=owner_user_id,
                created_at=_now(),
            )
            self._orgs[org.id] = org
            self._orgs_by_tenant[tenant_id] = org.id
            return org

    def get_org(self, org_id: str) -> Organization | None:
        with self._lock:
            return self._orgs.get(org_id)

    def get_org_by_tenant(self, tenant_id: str) -> Organization | None:
        with self._lock:
            org_id = self._orgs_by_tenant.get(tenant_id)
            return self._orgs.get(org_id) if org_id else None

    def set_plan(self, org_id: str, plan_id: str) -> Organization | None:
        with self._lock:
            org = self._orgs.get(org_id)
            if org is None:
                return None
            org.plan_id = plan_id
            return org

    def set_stripe_ids(
        self,
        org_id: str,
        *,
        customer_id: str | None = None,
        subscription_id: str | None = None,
    ) -> Organization | None:
        """Persist Stripe customer/subscription ids on the org (only overwrites
        fields that are provided)."""
        with self._lock:
            org = self._orgs.get(org_id)
            if org is None:
                return None
            if customer_id is not None:
                org.stripe_customer_id = customer_id
            if subscription_id is not None:
                org.stripe_subscription_id = subscription_id
            return org

    def get_org_by_stripe_customer(self, customer_id: str) -> Organization | None:
        with self._lock:
            for org in self._orgs.values():
                if org.stripe_customer_id == customer_id:
                    return org
            return None

    # ── Workspaces ───────────────────────────────────────────────────────
    def create_workspace(self, *, org_id: str, tenant_id: str, name: str) -> Workspace:
        with self._lock:
            ws = Workspace(
                id=str(uuid.uuid4()),
                org_id=org_id,
                tenant_id=tenant_id,
                name=name,
                slug=_slugify(name),
                created_at=_now(),
            )
            self._workspaces[ws.id] = ws
            return ws

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        with self._lock:
            return self._workspaces.get(workspace_id)

    def list_workspaces(self, org_id: str) -> list[Workspace]:
        with self._lock:
            return [w for w in self._workspaces.values() if w.org_id == org_id]

    def count_workspaces(self, org_id: str) -> int:
        with self._lock:
            return sum(1 for w in self._workspaces.values() if w.org_id == org_id)

    # ── Memberships ──────────────────────────────────────────────────────
    def add_member(
        self, *, org_id: str, user_id: str, email: str, role: str, status: str = "active"
    ) -> Membership:
        with self._lock:
            member = Membership(
                org_id=org_id,
                user_id=user_id,
                email=email,
                role=role,
                status=status,
                created_at=_now(),
            )
            self._memberships.setdefault(org_id, []).append(member)
            return member

    def list_members(self, org_id: str) -> list[Membership]:
        with self._lock:
            return list(self._memberships.get(org_id, []))

    def count_members(self, org_id: str) -> int:
        with self._lock:
            return len(self._memberships.get(org_id, []))

    def remove_member(self, org_id: str, user_id: str) -> bool:
        with self._lock:
            members = self._memberships.get(org_id, [])
            for i, m in enumerate(members):
                if m.user_id == user_id:
                    members.pop(i)
                    return True
            return False

    # ── Invites ──────────────────────────────────────────────────────────
    def create_invite(
        self, *, org_id: str, email: str, role: str, invited_by: str
    ) -> Invite:
        with self._lock:
            invite = Invite(
                id=str(uuid.uuid4()),
                org_id=org_id,
                email=email.strip().lower(),
                role=role,
                token=secrets.token_urlsafe(24),
                status="pending",
                created_at=_now(),
                expires_at=_now() + self.INVITE_TTL_SECONDS,
                invited_by=invited_by,
            )
            self._invites[invite.id] = invite
            return invite

    def get_invite(self, invite_id: str) -> Invite | None:
        with self._lock:
            return self._invites.get(invite_id)

    def get_invite_by_token(self, token: str) -> Invite | None:
        with self._lock:
            for inv in self._invites.values():
                if secrets.compare_digest(inv.token, token):
                    return inv
            return None

    def list_invites(self, org_id: str) -> list[Invite]:
        with self._lock:
            return [i for i in self._invites.values() if i.org_id == org_id]

    def accept_invite(self, token: str, *, user_id: str) -> Membership | None:
        with self._lock:
            invite = self.get_invite_by_token(token)
            if invite is None or invite.status != "pending" or invite.is_expired:
                return None
            invite.status = "accepted"
            return self.add_member(
                org_id=invite.org_id,
                user_id=user_id,
                email=invite.email,
                role=invite.role,
                status="active",
            )

    def revoke_invite(self, invite_id: str) -> bool:
        with self._lock:
            invite = self._invites.get(invite_id)
            if invite is None or invite.status != "pending":
                return False
            invite.status = "revoked"
            return True
