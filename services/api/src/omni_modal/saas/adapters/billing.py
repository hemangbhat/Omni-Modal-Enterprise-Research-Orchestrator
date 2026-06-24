"""Billing adapter: demo (default) | Stripe (when STRIPE_SECRET_KEY is set).

The demo adapter applies plan changes instantly with no charge (the historical
behaviour). The Stripe adapter implements a real subscription flow:

- ``create_checkout_session`` opens a Stripe Checkout for a plan's recurring
  price (products/prices are auto-created and reused via a stable ``lookup_key``
  so no manual dashboard setup is required).
- ``confirm_checkout`` retrieves a completed session and reports the plan +
  Stripe customer/subscription ids, so the return redirect can apply the plan
  even without webhook infrastructure (useful for local dev).
- ``create_portal_session`` opens the Stripe Billing Portal (upgrade, cancel,
  update card, invoices).
- ``verify_webhook`` validates a Stripe webhook signature when
  ``STRIPE_WEBHOOK_SECRET`` is configured.

Everything degrades gracefully: if the ``stripe`` library or key is missing,
``select_billing_adapter`` returns the demo adapter and the app keeps working.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from omni_modal.saas.plans import PLANS, Plan, get_plan

try:
    import stripe  # type: ignore[import-not-found]
    _STRIPE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when lib absent
    stripe = None  # type: ignore[assignment]
    _STRIPE_AVAILABLE = False


# Plans that map to a real recurring Stripe price. "free" is the downgrade
# target (cancel subscription); "enterprise" is contact-sales (no checkout).
CHECKOUTABLE_PLANS = ("pro",)


@dataclass
class CheckoutSession:
    url: str
    session_id: str


@dataclass
class CheckoutResult:
    paid: bool
    plan_id: str | None
    customer_id: str | None
    subscription_id: str | None


@dataclass
class PortalSession:
    url: str


class DemoBillingAdapter:
    """No-op billing: plan changes are applied locally by the service."""

    backend = "demo"
    supports_checkout = False

    def create_checkout_session(self, **_: object) -> CheckoutSession:
        raise RuntimeError("Demo billing does not support Stripe checkout.")

    def confirm_checkout(self, session_id: str) -> CheckoutResult:  # noqa: ARG002
        raise RuntimeError("Demo billing does not support Stripe checkout.")

    def create_portal_session(self, **_: object) -> PortalSession:
        raise RuntimeError("Demo billing does not support the Stripe portal.")

    def verify_webhook(self, payload: bytes, sig_header: str) -> dict:  # noqa: ARG002
        raise RuntimeError("Demo billing does not handle webhooks.")


class StripeBillingAdapter:
    """Real Stripe subscription billing (test or live, per the secret key)."""

    backend = "stripe"
    supports_checkout = True

    def __init__(self) -> None:
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
        self._webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        # Cache resolved price ids per plan for the process lifetime.
        self._price_cache: dict[str, str] = {}

    # ── Product / price provisioning ─────────────────────────────────────
    def _lookup_key(self, plan: Plan) -> str:
        return f"omero_{plan.id}_monthly"

    def _ensure_price(self, plan: Plan) -> str:
        """Return a recurring price id for ``plan``, creating it if needed.

        Idempotent: prices are looked up by ``lookup_key`` so repeated calls
        (and restarts) reuse the same Stripe price instead of creating new ones.
        """
        if plan.id in self._price_cache:
            return self._price_cache[plan.id]

        lookup_key = self._lookup_key(plan)
        existing = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
        if existing.data:
            price_id = existing.data[0].id
            self._price_cache[plan.id] = price_id
            return price_id

        product = stripe.Product.create(
            name=f"OMERO {plan.name}",
            metadata={"omero_plan_id": plan.id},
        )
        price = stripe.Price.create(
            product=product.id,
            unit_amount=plan.price_usd_month * 100,  # cents
            currency="usd",
            recurring={"interval": "month"},
            lookup_key=lookup_key,
            metadata={"omero_plan_id": plan.id},
        )
        self._price_cache[plan.id] = price.id
        return price.id

    # ── Checkout ─────────────────────────────────────────────────────────
    def create_checkout_session(
        self,
        *,
        plan_id: str,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
        customer_email: str | None = None,
        tenant_id: str = "",
    ) -> CheckoutSession:
        plan = get_plan(plan_id)
        price_id = self._ensure_price(plan)
        kwargs: dict[str, object] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {"omero_plan_id": plan_id, "omero_tenant_id": tenant_id},
            "subscription_data": {
                "metadata": {"omero_plan_id": plan_id, "omero_tenant_id": tenant_id}
            },
            "allow_promotion_codes": True,
        }
        if customer_id:
            kwargs["customer"] = customer_id
        elif customer_email:
            kwargs["customer_email"] = customer_email
        session = stripe.checkout.Session.create(**kwargs)
        return CheckoutSession(url=session.url, session_id=session.id)

    def confirm_checkout(self, session_id: str) -> CheckoutResult:
        session = stripe.checkout.Session.retrieve(session_id)
        paid = session.get("payment_status") == "paid" or session.get("status") == "complete"
        plan_id = (session.get("metadata") or {}).get("omero_plan_id")
        return CheckoutResult(
            paid=bool(paid),
            plan_id=plan_id,
            customer_id=session.get("customer"),
            subscription_id=session.get("subscription"),
        )

    # ── Portal ───────────────────────────────────────────────────────────
    def create_portal_session(self, *, customer_id: str, return_url: str) -> PortalSession:
        session = stripe.billing_portal.Session.create(
            customer=customer_id, return_url=return_url
        )
        return PortalSession(url=session.url)

    # ── Webhooks ─────────────────────────────────────────────────────────
    def verify_webhook(self, payload: bytes, sig_header: str) -> dict:
        if not self._webhook_secret:
            # No secret configured: parse without verification (dev only).
            import json  # noqa: PLC0415

            return json.loads(payload.decode("utf-8"))
        return stripe.Webhook.construct_event(  # type: ignore[no-any-return]
            payload, sig_header, self._webhook_secret
        )


def select_billing_adapter():
    """Return the Stripe adapter when configured + importable, else demo."""
    if os.environ.get("STRIPE_SECRET_KEY"):
        if not _STRIPE_AVAILABLE:
            print(
                "[billing] STRIPE_SECRET_KEY set but the 'stripe' library is not "
                "installed; falling back to demo billing. Run: pip install stripe",
                file=sys.stderr,
            )
            return DemoBillingAdapter()
        try:
            return StripeBillingAdapter()
        except Exception as exc:  # pragma: no cover - defensive
            print(
                f"[billing] STRIPE_SECRET_KEY set but Stripe adapter unavailable "
                f"({exc}); falling back to demo billing.",
                file=sys.stderr,
            )
            return DemoBillingAdapter()
    return DemoBillingAdapter()
