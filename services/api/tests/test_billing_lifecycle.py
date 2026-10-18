"""Billing lifecycle: upgrade, downgrade, cancellation, and Stripe webhook replay."""

from __future__ import annotations

import os
import unittest

import _path  # noqa: F401

for _key in ("DATABASE_URL", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
    os.environ.pop(_key, None)

from test_saas_billing import FakeStripeAdapter  # noqa: E402

from omni_modal.saas.billing_events import InMemoryBillingEventStore  # noqa: E402
from omni_modal.saas.plans import PlanLimitExceeded  # noqa: E402
from omni_modal.saas.service import SaasService  # noqa: E402

TENANT, OWNER, CUSTOMER = "bl-tenant", "bl-owner", "cus_bl"


def _checkout(event_id: str, created: int, plan: str = "pro") -> dict:
    return {"id": event_id, "created": created, "type": "checkout.session.completed", "data": {"object": {
        "customer": CUSTOMER, "subscription": "sub_bl",
        "metadata": {"omero_tenant_id": TENANT, "omero_plan_id": plan}}}}


def _updated(event_id: str, created: int, plan: str) -> dict:
    return {"id": event_id, "created": created, "type": "customer.subscription.updated",
            "data": {"object": {"customer": CUSTOMER, "metadata": {"omero_plan_id": plan}}}}


def _deleted(event_id: str, created: int) -> dict:
    return {"id": event_id, "created": created, "type": "customer.subscription.deleted",
            "data": {"object": {"customer": CUSTOMER}}}


class BillingLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = SaasService(billing=FakeStripeAdapter(), billing_events=InMemoryBillingEventStore())
        self.svc.ensure_org(TENANT, owner_user_id=OWNER)

    def plan(self) -> str:
        return self.svc.workspaces.get_org_by_tenant(TENANT).plan_id

    def test_upgrade_raises_limits(self) -> None:
        self.assertEqual(self.svc.apply_webhook_event(_checkout("evt_1", 100)), "applied")
        self.assertEqual(self.plan(), "pro")
        org = self.svc.workspaces.get_org_by_tenant(TENANT)
        self.assertEqual((org.stripe_customer_id, org.stripe_subscription_id), (CUSTOMER, "sub_bl"))
        for _ in range(150):  # past the free plan's 100 queries
            self.svc.record_usage(tenant_id=TENANT, user_id=OWNER, metric="queries")

    def test_downgrade_enforces_lower_limit(self) -> None:
        self.svc.apply_webhook_event(_checkout("evt_1", 100))
        for _ in range(100):
            self.svc.record_usage(tenant_id=TENANT, user_id=OWNER, metric="queries")
        self.assertEqual(self.svc.apply_webhook_event(_updated("evt_2", 200, "free")), "applied")
        self.assertEqual(self.plan(), "free")
        with self.assertRaises(PlanLimitExceeded):
            self.svc.record_usage(tenant_id=TENANT, user_id=OWNER, metric="queries")

    def test_cancellation_returns_to_free_and_clears_subscription(self) -> None:
        self.svc.apply_webhook_event(_checkout("evt_1", 100))
        self.assertEqual(self.svc.apply_webhook_event(_deleted("evt_2", 200)), "applied")
        org = self.svc.workspaces.get_org_by_tenant(TENANT)
        self.assertEqual(org.plan_id, "free")
        self.assertFalse(org.stripe_subscription_id)
        self.assertEqual(org.stripe_customer_id, CUSTOMER)  # kept so the owner can resubscribe

    def test_duplicate_delivery_is_applied_once(self) -> None:
        self.svc.apply_webhook_event(_checkout("evt_1", 100))
        before = self.svc.notifications.unread_count(TENANT)
        self.assertEqual(self.svc.apply_webhook_event(_checkout("evt_1", 100)), "duplicate")
        self.assertEqual(self.svc.notifications.unread_count(TENANT), before)

    def test_late_old_event_cannot_undo_cancellation(self) -> None:
        self.svc.apply_webhook_event(_checkout("evt_1", 100))
        self.svc.apply_webhook_event(_deleted("evt_3", 300))
        # A delayed upgrade created before the cancellation arrives afterwards.
        self.assertEqual(self.svc.apply_webhook_event(_updated("evt_2", 200, "pro")), "stale")
        self.assertEqual(self.plan(), "free")
        # It is now recorded, so a further replay is a plain duplicate.
        self.assertEqual(self.svc.apply_webhook_event(_updated("evt_2", 200, "pro")), "duplicate")

    def test_resubscribe_after_cancel_applies(self) -> None:
        self.svc.apply_webhook_event(_checkout("evt_1", 100))
        self.svc.apply_webhook_event(_deleted("evt_2", 200))
        self.assertEqual(self.svc.apply_webhook_event(_checkout("evt_3", 300)), "applied")
        self.assertEqual(self.plan(), "pro")

    def test_failed_apply_is_not_recorded(self) -> None:
        original = self.svc.change_plan

        def boom(**kw):
            raise RuntimeError("database down")

        self.svc.change_plan = boom
        with self.assertRaises(RuntimeError):
            self.svc.apply_webhook_event(_checkout("evt_1", 100))
        self.svc.change_plan = original
        self.assertEqual(self.svc.apply_webhook_event(_checkout("evt_1", 100)), "applied")  # Stripe's retry
        self.assertEqual(self.plan(), "pro")

    def test_events_without_id_still_apply(self) -> None:
        event = _checkout("x", 100)
        del event["id"]
        self.assertEqual(self.svc.apply_webhook_event(event), "applied")
        self.assertEqual(self.svc.apply_webhook_event(event), "applied")


class WebhookRouteReplayTests(unittest.TestCase):
    def test_route_reports_duplicate_with_200(self) -> None:
        import json

        os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from omni_modal.api.app import create_app  # noqa: PLC0415
        from omni_modal.api.app import state as app_state  # noqa: PLC0415

        with TestClient(create_app()) as client:
            saved = app_state.saas.billing
            app_state.saas.billing = FakeStripeAdapter()
            try:
                app_state.saas.ensure_org(TENANT, owner_user_id=OWNER)
                body = json.dumps(_checkout("evt_route_1", 100)).encode()
                first = client.post("/billing/webhook", content=body, headers={"Stripe-Signature": "t=1"})
                second = client.post("/billing/webhook", content=body, headers={"Stripe-Signature": "t=1"})
            finally:
                app_state.saas.billing = saved
        self.assertEqual((first.status_code, first.json()["outcome"]), (200, "applied"))
        self.assertEqual((second.status_code, second.json()["outcome"]), (200, "duplicate"))


if __name__ == "__main__":
    unittest.main()
