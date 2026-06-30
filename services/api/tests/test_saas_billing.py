"""Tests for the Stripe billing integration in the SaaS layer.

These tests avoid hitting the real Stripe API by injecting a fake billing
adapter into SaasService. They verify the service-level orchestration:
checkout start, confirm-applies-plan, portal guard, and webhook plan sync.
A separate test confirms adapter selection falls back to demo when no key is
set.
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_modal.saas.adapters.billing import (  # noqa: E402
    DemoBillingAdapter,
    select_billing_adapter,
)
from omni_modal.saas.service import SaasService  # noqa: E402


@dataclass
class _Session:
    url: str
    session_id: str


@dataclass
class _Result:
    paid: bool
    plan_id: str | None
    customer_id: str | None
    subscription_id: str | None


@dataclass
class _Portal:
    url: str


class FakeStripeAdapter:
    """Stand-in for StripeBillingAdapter — no network calls."""

    backend = "stripe"
    supports_checkout = True

    def __init__(self) -> None:
        self.last_checkout: dict | None = None

    def create_checkout_session(self, **kwargs) -> _Session:
        self.last_checkout = kwargs
        return _Session(url="https://checkout.stripe.test/session", session_id="cs_test_123")

    def confirm_checkout(self, session_id: str) -> _Result:
        return _Result(
            paid=True, plan_id="pro", customer_id="cus_test_1", subscription_id="sub_test_1"
        )

    def create_portal_session(self, *, customer_id: str, return_url: str) -> _Portal:
        return _Portal(url=f"https://portal.stripe.test/{customer_id}")

    def verify_webhook(self, payload: bytes, sig_header: str) -> dict:
        import json

        return json.loads(payload.decode("utf-8"))


def _service_with_fake() -> SaasService:
    svc = SaasService(billing=FakeStripeAdapter())
    return svc


class BillingModeTests(unittest.TestCase):
    def test_demo_adapter_when_no_key(self) -> None:
        saved = os.environ.pop("STRIPE_SECRET_KEY", None)
        try:
            adapter = select_billing_adapter()
            self.assertIsInstance(adapter, DemoBillingAdapter)
            self.assertEqual(adapter.backend, "demo")
            self.assertFalse(getattr(adapter, "supports_checkout", False))
        finally:
            if saved is not None:
                os.environ["STRIPE_SECRET_KEY"] = saved

    def test_billing_mode_reflects_adapter(self) -> None:
        svc = _service_with_fake()
        self.assertEqual(svc.billing_mode(), "stripe")
        demo = SaasService(billing=DemoBillingAdapter())
        self.assertEqual(demo.billing_mode(), "demo")


class CheckoutFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = _service_with_fake()
        self.tenant = "billing-tenant"
        self.user = "owner-1"

    def test_start_checkout_returns_url(self) -> None:
        result = self.svc.start_checkout(
            tenant_id=self.tenant, user_id=self.user, plan_id="pro",
            success_url="https://app/billing?status=success&session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://app/billing?status=cancelled",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["session_id"], "cs_test_123")
        self.assertTrue(result["url"].startswith("https://"))

    def test_start_checkout_unknown_plan_returns_none(self) -> None:
        result = self.svc.start_checkout(
            tenant_id=self.tenant, user_id=self.user, plan_id="does-not-exist",
            success_url="x", cancel_url="y",
        )
        self.assertIsNone(result)

    def test_confirm_checkout_applies_plan_and_stores_ids(self) -> None:
        self.svc.ensure_org(self.tenant, owner_user_id=self.user)
        out = self.svc.confirm_checkout(
            tenant_id=self.tenant, user_id=self.user, session_id="cs_test_123"
        )
        self.assertEqual(out, {"paid": True, "plan_id": "pro"})
        org = self.svc.workspaces.get_org_by_tenant(self.tenant)
        assert org is not None
        self.assertEqual(org.plan_id, "pro")
        self.assertEqual(org.stripe_customer_id, "cus_test_1")
        self.assertEqual(org.stripe_subscription_id, "sub_test_1")

    def test_portal_requires_existing_customer(self) -> None:
        # No checkout yet -> no customer id -> portal returns None.
        self.svc.ensure_org(self.tenant, owner_user_id=self.user)
        self.assertIsNone(
            self.svc.start_portal(tenant_id=self.tenant, user_id=self.user, return_url="https://app/billing")
        )
        # After a confirmed checkout, the portal opens.
        self.svc.confirm_checkout(tenant_id=self.tenant, user_id=self.user, session_id="cs_test_123")
        portal = self.svc.start_portal(
            tenant_id=self.tenant, user_id=self.user, return_url="https://app/billing"
        )
        assert portal is not None
        self.assertIn("portal.stripe.test", str(portal["url"]))

    def test_demo_adapter_rejects_checkout(self) -> None:
        demo = SaasService(billing=DemoBillingAdapter())
        with self.assertRaises(RuntimeError):
            demo.start_checkout(
                tenant_id=self.tenant, user_id=self.user, plan_id="pro",
                success_url="x", cancel_url="y",
            )


class WebhookSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = _service_with_fake()
        self.tenant = "wh-tenant"
        self.user = "wh-owner"
        self.svc.ensure_org(self.tenant, owner_user_id=self.user)

    def test_checkout_completed_sets_plan(self) -> None:
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "customer": "cus_wh",
                "subscription": "sub_wh",
                "metadata": {"omero_tenant_id": self.tenant, "omero_plan_id": "pro"},
            }},
        }
        self.svc.apply_webhook_event(event)
        org = self.svc.workspaces.get_org_by_tenant(self.tenant)
        assert org is not None
        self.assertEqual(org.plan_id, "pro")
        self.assertEqual(org.stripe_customer_id, "cus_wh")

    def test_subscription_deleted_downgrades_to_free(self) -> None:
        # First attach a customer + pro plan.
        self.svc.workspaces.set_stripe_ids(
            self.svc.workspaces.get_org_by_tenant(self.tenant).id, customer_id="cus_wh"
        )
        self.svc.change_plan(tenant_id=self.tenant, user_id=self.user, plan_id="pro")
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_wh"}},
        }
        self.svc.apply_webhook_event(event)
        org = self.svc.workspaces.get_org_by_tenant(self.tenant)
        assert org is not None
        self.assertEqual(org.plan_id, "free")

    def test_payment_failed_notifies_owner(self) -> None:
        # Attach a customer so the webhook can resolve the org.
        self.svc.workspaces.set_stripe_ids(
            self.svc.workspaces.get_org_by_tenant(self.tenant).id, customer_id="cus_wh"
        )
        before = self.svc.notifications.unread_count(self.tenant)
        event = {
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_wh"}},
        }
        self.svc.apply_webhook_event(event)
        after = self.svc.notifications.list_for(self.tenant)
        self.assertGreater(self.svc.notifications.unread_count(self.tenant), before)
        self.assertTrue(any("Payment failed" in n.title for n in after))


if __name__ == "__main__":
    unittest.main()
