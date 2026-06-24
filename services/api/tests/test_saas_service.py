"""Tests for the SaasService facade, usage metering, and notifications."""
from __future__ import annotations

import unittest

import _path  # noqa: F401

from omni_modal.saas.adapters.analytics import InMemoryAnalyticsAdapter
from omni_modal.saas.adapters.email import ConsoleEmailAdapter
from omni_modal.saas.notifications import NotificationStore
from omni_modal.saas.plans import PlanLimitExceeded
from omni_modal.saas.service import SaasService
from omni_modal.saas.usage import UsageStore, current_period


def _service() -> SaasService:
    return SaasService(
        email=ConsoleEmailAdapter(),
        analytics=InMemoryAnalyticsAdapter(),
    )


class TestUsageStore(unittest.TestCase):
    def test_record_and_get(self) -> None:
        usage = UsageStore()
        self.assertEqual(usage.record("t1", "queries"), 1)
        self.assertEqual(usage.record("t1", "queries"), 2)
        self.assertEqual(usage.get("t1", "queries"), 2)

    def test_period_isolation(self) -> None:
        usage = UsageStore()
        usage.record("t1", "queries", period="2020-01")
        self.assertEqual(usage.get("t1", "queries", period="2020-01"), 1)
        self.assertEqual(usage.get("t1", "queries", period="2020-02"), 0)

    def test_negative_amount_rejected(self) -> None:
        with self.assertRaises(ValueError):
            UsageStore().record("t1", "queries", amount=-1)


class TestNotifications(unittest.TestCase):
    def test_add_list_and_unread(self) -> None:
        store = NotificationStore()
        store.add(tenant_id="t1", title="Hi")
        store.add(tenant_id="t1", title="Bye")
        self.assertEqual(len(store.list_for("t1")), 2)
        self.assertEqual(store.unread_count("t1"), 2)

    def test_mark_read(self) -> None:
        store = NotificationStore()
        n = store.add(tenant_id="t1", title="Hi")
        self.assertTrue(store.mark_read("t1", n.id))
        self.assertEqual(store.unread_count("t1"), 0)

    def test_user_scoped_visibility(self) -> None:
        store = NotificationStore()
        store.add(tenant_id="t1", title="tenant-wide")  # user_id None
        store.add(tenant_id="t1", title="for-u2", user_id="u2")
        # u1 sees only the tenant-wide one
        self.assertEqual(len(store.list_for("t1", user_id="u1")), 1)
        # u2 sees both
        self.assertEqual(len(store.list_for("t1", user_id="u2")), 2)

    def test_ring_buffer_cap(self) -> None:
        store = NotificationStore(max_per_tenant=5)
        for i in range(10):
            store.add(tenant_id="t1", title=str(i))
        self.assertEqual(len(store.list_for("t1")), 5)


class TestSaasService(unittest.TestCase):
    def test_ensure_org_autoprovisions(self) -> None:
        svc = _service()
        org = svc.ensure_org("new-tenant", owner_user_id="u1")
        self.assertEqual(org.plan_id, "free")
        # default workspace + owner membership created
        self.assertEqual(svc.workspaces.count_workspaces(org.id), 1)
        self.assertEqual(svc.workspaces.count_members(org.id), 1)

    def test_ensure_org_idempotent(self) -> None:
        svc = _service()
        a = svc.ensure_org("t1", owner_user_id="u1")
        b = svc.ensure_org("t1", owner_user_id="u1")
        self.assertEqual(a.id, b.id)

    def test_create_workspace_enforces_plan_limit(self) -> None:
        svc = _service()
        # free plan: max 1 workspace, ensure_org already created the default one
        with self.assertRaises(PlanLimitExceeded):
            svc.create_workspace(tenant_id="t1", user_id="u1", name="Second")

    def test_invite_member_sends_email_and_notifies(self) -> None:
        svc = _service()
        svc.ensure_org("t1", owner_user_id="u1")
        invite = svc.invite_member(tenant_id="t1", user_id="u1", email="x@x.com", role="researcher")
        self.assertEqual(invite.status, "pending")
        self.assertEqual(len(svc.email.sent), 1)
        self.assertEqual(svc.email.sent[0].to, "x@x.com")
        self.assertTrue(any(n.title == "Invitation sent" for n in svc.notifications.list_for("t1")))

    def test_record_usage_enforces_limit(self) -> None:
        svc = _service()
        svc.ensure_org("t1", owner_user_id="u1")  # free plan, 100 queries
        for _ in range(100):
            svc.record_usage(tenant_id="t1", user_id="u1", metric="queries")
        with self.assertRaises(PlanLimitExceeded):
            svc.record_usage(tenant_id="t1", user_id="u1", metric="queries")

    def test_usage_report_shape(self) -> None:
        svc = _service()
        svc.ensure_org("t1", owner_user_id="u1")
        svc.record_usage(tenant_id="t1", user_id="u1", metric="queries")
        report = svc.usage_report("t1")
        self.assertIn("plan", report)
        self.assertIn("queries", report["metrics"])
        self.assertEqual(report["metrics"]["queries"]["used"], 1)

    def test_change_plan(self) -> None:
        svc = _service()
        svc.ensure_org("t1", owner_user_id="u1")
        svc.change_plan(tenant_id="t1", user_id="u1", plan_id="pro")
        self.assertEqual(svc.workspaces.get_org_by_tenant("t1").plan_id, "pro")
        # now a second workspace is allowed
        svc.create_workspace(tenant_id="t1", user_id="u1", name="Second")

    def test_change_plan_rejects_unknown(self) -> None:
        svc = _service()
        svc.ensure_org("t1", owner_user_id="u1")
        self.assertIsNone(svc.change_plan(tenant_id="t1", user_id="u1", plan_id="nope"))

    def test_billing_mode_demo_without_stripe(self) -> None:
        from omni_modal.saas.adapters.billing import DemoBillingAdapter

        svc = SaasService(
            email=ConsoleEmailAdapter(),
            analytics=InMemoryAnalyticsAdapter(),
            billing=DemoBillingAdapter(),
        )
        self.assertEqual(svc.billing_mode(), "demo")

    def test_seed_demo_populates(self) -> None:
        svc = _service()
        svc.seed_demo()
        org = svc.workspaces.get_org_by_tenant("demo-tenant")
        self.assertIsNotNone(org)
        self.assertEqual(org.plan_id, "pro")
        self.assertEqual(svc.workspaces.count_members(org.id), 3)
        self.assertEqual(svc.workspaces.count_workspaces(org.id), 2)

    def test_seed_demo_idempotent(self) -> None:
        svc = _service()
        svc.seed_demo()
        svc.seed_demo()
        org = svc.workspaces.get_org_by_tenant("demo-tenant")
        self.assertEqual(svc.workspaces.count_members(org.id), 3)

    def test_accept_invite_creates_membership(self) -> None:
        svc = _service()
        svc.ensure_org("t1", owner_user_id="u1")
        invite = svc.invite_member(tenant_id="t1", user_id="u1", email="x@x.com", role="researcher")
        member = svc.accept_invite(token=invite.token, user_id="u2")
        self.assertIsNotNone(member)
        self.assertEqual(member.role, "researcher")
        # A success notification is recorded for the org tenant.
        self.assertTrue(
            any(n.title == "New teammate joined" for n in svc.notifications.list_for("t1"))
        )

    def test_accept_invite_invalid_token(self) -> None:
        svc = _service()
        self.assertIsNone(svc.accept_invite(token="bogus", user_id="u2"))

    def test_preview_invite(self) -> None:
        svc = _service()
        svc.ensure_org("t1", owner_user_id="u1", name="Acme")
        invite = svc.invite_member(tenant_id="t1", user_id="u1", email="x@x.com", role="auditor")
        preview = svc.preview_invite(invite.token)
        self.assertEqual(preview["email"], "x@x.com")
        self.assertEqual(preview["role"], "auditor")
        self.assertTrue(preview["valid"])
        self.assertNotIn("token", preview)

    def test_document_workspace_tagging(self) -> None:
        svc = _service()
        svc.tag_document("doc-1", "ws-a")
        svc.tag_document("doc-2", "ws-a")
        svc.tag_document("doc-3", "ws-b")
        self.assertEqual(svc.workspace_for_document("doc-1"), "ws-a")
        self.assertEqual(svc.documents_in_workspace("ws-a"), {"doc-1", "doc-2"})
        self.assertEqual(svc.documents_in_workspace("ws-b"), {"doc-3"})

    def test_tag_document_ignores_empty_workspace(self) -> None:
        svc = _service()
        svc.tag_document("doc-1", None)
        svc.tag_document("doc-2", "")
        self.assertIsNone(svc.workspace_for_document("doc-1"))
        self.assertEqual(svc.documents_in_workspace("ws-a"), set())


if __name__ == "__main__":
    unittest.main()
