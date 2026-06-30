"""SaasService — the single facade the HTTP layer uses for SaaS features.

Composes the workspace store, usage meter, notification center, and optional
adapters. Provides higher-level operations (create workspace with limit check,
invite member with email + notification, record usage with limit enforcement)
and a ``seed_demo`` that makes the local demo show a populated, realistic SaaS
account out of the box.
"""

from __future__ import annotations

import threading

from omni_modal.saas.adapters import (
    select_analytics_adapter,
    select_billing_adapter,
    select_email_adapter,
    select_storage_adapter,
)
from omni_modal.saas.notifications import (
    KIND_INFO,
    KIND_SUCCESS,
    NotificationStore,
)
from omni_modal.saas.plans import (
    PLANS,
    Plan,
    PlanLimitExceeded,
    assert_within_limit,
    get_plan,
)
from omni_modal.saas.repository import (
    select_notification_store,
    select_usage_store,
    select_workspace_store,
)
from omni_modal.saas.usage import UsageStore
from omni_modal.saas.workspaces import (
    Invite,
    Membership,
    Organization,
    Workspace,
    WorkspaceStore,
)

DEMO_TENANT = "demo-tenant"
DEMO_USER = "demo-user"


class SaasService:
    def __init__(
        self,
        *,
        workspaces: WorkspaceStore | None = None,
        usage: UsageStore | None = None,
        notifications: NotificationStore | None = None,
        email=None,
        analytics=None,
        storage=None,
        billing=None,
    ) -> None:
        self.workspaces = workspaces or select_workspace_store()
        self.usage = usage or select_usage_store()
        self.notifications = notifications or select_notification_store()
        self.email = email or select_email_adapter()
        self.analytics = analytics or select_analytics_adapter()
        self.storage = storage or select_storage_adapter()
        self.billing = billing or select_billing_adapter()
        # document_id -> workspace_id (real workspace scoping for ingested docs)
        self._doc_lock = threading.RLock()
        self._doc_workspace: dict[str, str] = {}
        # Resolve a Postgres pool once so the doc↔workspace map can persist too.
        try:
            from omni_modal.saas.repository import _pool_or_none  # noqa: PLC0415

            self._pool = _pool_or_none()
        except Exception:
            self._pool = None

    # ── Document ↔ workspace scoping ─────────────────────────────────────
    def tag_document(self, document_id: str, workspace_id: str | None) -> None:
        if not workspace_id:
            return
        if self._pool is not None:
            ws = self.workspaces.get_workspace(workspace_id)
            tenant_id = ws.tenant_id if ws else ""
            try:
                with self._pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO document_workspace
                           (document_id, workspace_id, tenant_id, created_at)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (document_id)
                           DO UPDATE SET workspace_id = EXCLUDED.workspace_id""",
                        (document_id, workspace_id, tenant_id, __import__("time").time()),
                    )
                return
            except Exception:
                pass  # fall through to in-memory on any DB error
        with self._doc_lock:
            self._doc_workspace[document_id] = workspace_id

    def workspace_for_document(self, document_id: str) -> str | None:
        if self._pool is not None:
            try:
                with self._pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT workspace_id FROM document_workspace WHERE document_id = %s",
                        (document_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return row[0]
            except Exception:
                pass
        with self._doc_lock:
            return self._doc_workspace.get(document_id)

    def documents_in_workspace(self, workspace_id: str) -> set[str]:
        if self._pool is not None:
            try:
                with self._pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT document_id FROM document_workspace WHERE workspace_id = %s",
                        (workspace_id,),
                    )
                    return {r[0] for r in cur.fetchall()}
            except Exception:
                pass
        with self._doc_lock:
            return {d for d, w in self._doc_workspace.items() if w == workspace_id}

    # ── Org resolution ───────────────────────────────────────────────────
    def ensure_org(self, tenant_id: str, *, owner_user_id: str, name: str | None = None) -> Organization:
        """Return the org for a tenant, auto-provisioning one on first use.

        New tenants get a free-plan org and a default workspace so the SaaS
        model is always populated for any authenticated user.
        """
        org = self.workspaces.get_org_by_tenant(tenant_id)
        if org is not None:
            return org
        org = self.workspaces.create_org(
            tenant_id=tenant_id,
            name=name or f"{tenant_id} organization",
            owner_user_id=owner_user_id,
        )
        self.workspaces.add_member(
            org_id=org.id, user_id=owner_user_id, email=f"{owner_user_id}@example.com",
            role="admin", status="active",
        )
        self.workspaces.create_workspace(org_id=org.id, tenant_id=tenant_id, name="Default Workspace")
        return org

    # ── Workspaces ───────────────────────────────────────────────────────
    def create_workspace(self, *, tenant_id: str, user_id: str, name: str) -> Workspace:
        org = self.ensure_org(tenant_id, owner_user_id=user_id)
        assert_within_limit(org.plan_id, "workspaces", self.workspaces.count_workspaces(org.id))
        ws = self.workspaces.create_workspace(org_id=org.id, tenant_id=tenant_id, name=name)
        self.analytics.capture(event="workspace_created", tenant_id=tenant_id, user_id=user_id)
        self.notifications.add(
            tenant_id=tenant_id, title="Workspace created",
            body=f"'{name}' is ready.", kind=KIND_SUCCESS,
        )
        return ws

    # ── Members & invites ────────────────────────────────────────────────
    def invite_member(self, *, tenant_id: str, user_id: str, email: str, role: str) -> Invite:
        org = self.ensure_org(tenant_id, owner_user_id=user_id)
        pending_and_active = self.workspaces.count_members(org.id) + len(
            [i for i in self.workspaces.list_invites(org.id) if i.status == "pending"]
        )
        assert_within_limit(org.plan_id, "members", pending_and_active)
        invite = self.workspaces.create_invite(
            org_id=org.id, email=email, role=role, invited_by=user_id
        )
        accept_url = f"/accept-invite?token={invite.token}"
        self.email.send(
            to=email,
            subject="You're invited to OMERO",
            body=(
                f"You have been invited to join an OMERO organization as {role}. "
                f"Accept here: {accept_url}"
            ),
        )
        self.analytics.capture(event="member_invited", tenant_id=tenant_id, user_id=user_id,
                               properties={"role": role})
        self.notifications.add(
            tenant_id=tenant_id, title="Invitation sent",
            body=f"Invited {email} as {role}.", kind=KIND_INFO,
        )
        return invite

    def preview_invite(self, token: str) -> dict[str, object] | None:
        """Return a redacted view of an invite by token, for the accept page."""
        invite = self.workspaces.get_invite_by_token(token)
        if invite is None:
            return None
        org = self.workspaces.get_org(invite.org_id)
        return {
            "email": invite.email,
            "role": invite.role,
            "status": invite.status,
            "expired": invite.is_expired,
            "organization": org.name if org else "Organization",
            "valid": invite.status == "pending" and not invite.is_expired,
        }

    def accept_invite(self, *, token: str, user_id: str) -> Membership | None:
        """Accept an invite token for the signed-in user, creating membership."""
        member = self.workspaces.accept_invite(token, user_id=user_id)
        if member is None:
            return None
        org = self.workspaces.get_org(member.org_id)
        if org is not None:
            self.notifications.add(
                tenant_id=org.tenant_id, title="New teammate joined",
                body=f"{member.email} accepted their invitation.", kind=KIND_SUCCESS,
            )
            self.analytics.capture(
                event="invite_accepted", tenant_id=org.tenant_id, user_id=user_id,
                properties={"role": member.role},
            )
        return member

    # ── Usage & gating ───────────────────────────────────────────────────
    def record_usage(self, *, tenant_id: str, user_id: str, metric: str, enforce: bool = True) -> int:
        org = self.workspaces.get_org_by_tenant(tenant_id)
        plan_id = org.plan_id if org else "free"
        current = self.usage.get(tenant_id, metric)
        if enforce:
            assert_within_limit(plan_id, metric, current)
        new_value = self.usage.record(tenant_id, metric)
        # Warn at 80% of a finite limit.
        limit = get_plan(plan_id).limit_for(metric)
        if limit > 0 and new_value == int(limit * 0.8):
            self.notifications.add(
                tenant_id=tenant_id,
                title=f"Approaching {metric} limit",
                body=f"You've used {new_value} of {limit} {metric} this month.",
                kind="warning",
            )
        return new_value

    def usage_report(self, tenant_id: str) -> dict[str, object]:
        org = self.workspaces.get_org_by_tenant(tenant_id)
        plan = get_plan(org.plan_id if org else "free")
        snapshot = self.usage.snapshot(tenant_id)
        metrics: dict[str, object] = {}
        for metric in ("queries", "uploads", "workspaces", "members", "storage_mb"):
            if metric == "workspaces" and org:
                used = self.workspaces.count_workspaces(org.id)
            elif metric == "members" and org:
                used = self.workspaces.count_members(org.id)
            else:
                used = snapshot.get(metric, 0)
            limit = plan.limit_for(metric)
            metrics[metric] = {
                "used": used,
                "limit": limit,
                "unlimited": limit < 0,
                "percent": (round(used / limit * 100, 1) if limit > 0 else 0),
            }
        return {"plan": plan.to_dict(), "metrics": metrics}

    # ── Billing ──────────────────────────────────────────────────────────
    def change_plan(self, *, tenant_id: str, user_id: str, plan_id: str) -> Organization | None:
        """Apply a plan change locally (demo billing, or after a Stripe event)."""
        if plan_id not in PLANS:
            return None
        org = self.ensure_org(tenant_id, owner_user_id=user_id)
        updated = self.workspaces.set_plan(org.id, plan_id)
        self.analytics.capture(event="plan_changed", tenant_id=tenant_id, user_id=user_id,
                               properties={"plan_id": plan_id})
        self.notifications.add(
            tenant_id=tenant_id, title="Plan updated",
            body=f"Your plan is now {PLANS[plan_id].name}.", kind=KIND_SUCCESS,
        )
        return updated

    def billing_mode(self) -> str:
        """Honest billing status: 'stripe' only when the Stripe adapter is live."""
        return getattr(self.billing, "backend", "demo")

    def start_checkout(
        self, *, tenant_id: str, user_id: str, plan_id: str,
        success_url: str, cancel_url: str,
    ) -> dict[str, object] | None:
        """Begin a Stripe Checkout for a paid plan. Returns {url, session_id}.

        Returns None for unknown plans. Raises RuntimeError if the active
        billing adapter does not support checkout (demo mode).
        """
        if plan_id not in PLANS:
            return None
        if not getattr(self.billing, "supports_checkout", False):
            raise RuntimeError("Active billing adapter does not support checkout.")
        org = self.ensure_org(tenant_id, owner_user_id=user_id)
        members = self.workspaces.list_members(org.id)
        email = next((m.email for m in members if m.user_id == user_id), None)
        session = self.billing.create_checkout_session(
            plan_id=plan_id,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_id=org.stripe_customer_id,
            customer_email=email,
            tenant_id=tenant_id,
        )
        return {"url": session.url, "session_id": session.session_id}

    def confirm_checkout(
        self, *, tenant_id: str, user_id: str, session_id: str
    ) -> dict[str, object] | None:
        """Verify a completed Checkout session and apply the purchased plan."""
        if not getattr(self.billing, "supports_checkout", False):
            raise RuntimeError("Active billing adapter does not support checkout.")
        result = self.billing.confirm_checkout(session_id)
        if not result.paid or not result.plan_id:
            return {"paid": False, "plan_id": None}
        org = self.ensure_org(tenant_id, owner_user_id=user_id)
        self.workspaces.set_stripe_ids(
            org.id,
            customer_id=result.customer_id,
            subscription_id=result.subscription_id,
        )
        self.change_plan(tenant_id=tenant_id, user_id=user_id, plan_id=result.plan_id)
        return {"paid": True, "plan_id": result.plan_id}

    def start_portal(
        self, *, tenant_id: str, user_id: str, return_url: str
    ) -> dict[str, object] | None:
        """Open the Stripe Billing Portal for the org's customer."""
        if not getattr(self.billing, "supports_checkout", False):
            raise RuntimeError("Active billing adapter does not support the portal.")
        org = self.ensure_org(tenant_id, owner_user_id=user_id)
        if not org.stripe_customer_id:
            return None  # no Stripe customer yet — nothing to manage
        session = self.billing.create_portal_session(
            customer_id=org.stripe_customer_id, return_url=return_url
        )
        return {"url": session.url}

    def apply_webhook_event(self, event: dict) -> None:
        """React to verified Stripe webhook events (idempotent plan sync)."""
        event_type = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}

        if event_type == "checkout.session.completed":
            customer_id = obj.get("customer")
            subscription_id = obj.get("subscription")
            metadata = obj.get("metadata") or {}
            tenant_id = metadata.get("omero_tenant_id")
            plan_id = metadata.get("omero_plan_id")
            if tenant_id:
                org = self.workspaces.get_org_by_tenant(tenant_id)
                if org is not None:
                    self.workspaces.set_stripe_ids(
                        org.id, customer_id=customer_id, subscription_id=subscription_id
                    )
                    if plan_id:
                        self.change_plan(
                            tenant_id=tenant_id, user_id=org.owner_user_id, plan_id=plan_id
                        )
        elif event_type in ("customer.subscription.deleted",):
            customer_id = obj.get("customer")
            org = self.workspaces.get_org_by_stripe_customer(customer_id) if customer_id else None
            if org is not None:
                self.workspaces.set_stripe_ids(org.id, subscription_id="")
                self.change_plan(
                    tenant_id=org.tenant_id, user_id=org.owner_user_id, plan_id="free"
                )
        elif event_type == "customer.subscription.updated":
            customer_id = obj.get("customer")
            metadata = obj.get("metadata") or {}
            plan_id = metadata.get("omero_plan_id")
            org = self.workspaces.get_org_by_stripe_customer(customer_id) if customer_id else None
            if org is not None and plan_id:
                self.change_plan(
                    tenant_id=org.tenant_id, user_id=org.owner_user_id, plan_id=plan_id
                )
        elif event_type == "invoice.payment_failed":
            # Dunning: a recurring charge failed. Notify the org owner so they
            # can update their card before Stripe's retries exhaust and the
            # subscription is cancelled (handled by subscription.deleted above).
            customer_id = obj.get("customer")
            org = self.workspaces.get_org_by_stripe_customer(customer_id) if customer_id else None
            if org is not None:
                try:
                    self.notifications.add(
                        tenant_id=org.tenant_id,
                        title="Payment failed",
                        body="Your latest subscription payment failed. Please update your "
                             "billing details to avoid losing access.",
                        kind="warning",
                        user_id=org.owner_user_id,
                    )
                except Exception:  # noqa: BLE001 - notification failure must not break the webhook
                    pass

    # ── Demo seed ────────────────────────────────────────────────────────
    def seed_demo(self) -> None:
        """Populate a realistic demo org so the SaaS UI is never empty."""
        if self.workspaces.get_org_by_tenant(DEMO_TENANT) is not None:
            return
        org = self.workspaces.create_org(
            tenant_id=DEMO_TENANT, name="Demo Research Org",
            owner_user_id=DEMO_USER, plan_id="pro",
        )
        self.workspaces.add_member(
            org_id=org.id, user_id=DEMO_USER, email="demo@omero.dev",
            role="admin", status="active",
        )
        self.workspaces.add_member(
            org_id=org.id, user_id="analyst-1", email="analyst@omero.dev",
            role="researcher", status="active",
        )
        self.workspaces.add_member(
            org_id=org.id, user_id="auditor-1", email="auditor@omero.dev",
            role="auditor", status="active",
        )
        self.workspaces.create_workspace(org_id=org.id, tenant_id=DEMO_TENANT, name="Oncology Research")
        self.workspaces.create_workspace(org_id=org.id, tenant_id=DEMO_TENANT, name="Clinical Trials")
        self.notifications.add(
            tenant_id=DEMO_TENANT, title="Welcome to OMERO",
            body="Your demo organization is ready. Upload a document to get started.",
            kind=KIND_INFO,
        )


_singleton: SaasService | None = None
_singleton_lock = threading.Lock()


def get_saas_service() -> SaasService:
    """Process-wide singleton, seeded with the demo org on first access."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                svc = SaasService()
                svc.seed_demo()
                _singleton = svc
    return _singleton
