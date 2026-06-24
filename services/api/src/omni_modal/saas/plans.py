"""Subscription plans, limits, and feature gating.

These plans are real and enforced at the API layer (limit checks raise
``PlanLimitExceeded``). They are NOT connected to a payment processor by
default — upgrading is a local state change ("demo billing"). Real Stripe
billing is an optional adapter (see ``omni_modal.saas.adapters.billing``) that
is only active when ``STRIPE_SECRET_KEY`` is set; without it the plan simply
reflects whatever has been set locally. Nothing here charges money.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class PlanLimitExceeded(Exception):
    """Raised when an action would exceed the current plan's limit."""

    def __init__(self, metric: str, limit: int, current: int) -> None:
        self.metric = metric
        self.limit = limit
        self.current = current
        super().__init__(
            f"Plan limit reached for '{metric}': {current}/{limit}. Upgrade to continue."
        )


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_usd_month: int
    # -1 means unlimited
    max_workspaces: int
    max_members: int
    monthly_query_limit: int
    monthly_upload_limit: int
    storage_mb: int
    features: frozenset[str]

    def has_feature(self, feature: str) -> bool:
        return feature in self.features

    def limit_for(self, metric: str) -> int:
        return {
            "workspaces": self.max_workspaces,
            "members": self.max_members,
            "queries": self.monthly_query_limit,
            "uploads": self.monthly_upload_limit,
            "storage_mb": self.storage_mb,
        }.get(metric, -1)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "price_usd_month": self.price_usd_month,
            "max_workspaces": self.max_workspaces,
            "max_members": self.max_members,
            "monthly_query_limit": self.monthly_query_limit,
            "monthly_upload_limit": self.monthly_upload_limit,
            "storage_mb": self.storage_mb,
            "features": sorted(self.features),
        }


# Feature keys used across the app for gating.
FEATURE_EXTERNAL_DELEGATION = "external_delegation"
FEATURE_AUDIT_EXPORT = "audit_export"
FEATURE_PRIORITY_INGEST = "priority_ingest"
FEATURE_SSO = "sso"
FEATURE_API_ACCESS = "api_access"


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        price_usd_month=0,
        max_workspaces=1,
        max_members=3,
        monthly_query_limit=100,
        monthly_upload_limit=25,
        storage_mb=100,
        features=frozenset({FEATURE_API_ACCESS}),
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        price_usd_month=49,
        max_workspaces=5,
        max_members=25,
        monthly_query_limit=5_000,
        monthly_upload_limit=1_000,
        storage_mb=10_240,
        features=frozenset(
            {
                FEATURE_API_ACCESS,
                FEATURE_EXTERNAL_DELEGATION,
                FEATURE_AUDIT_EXPORT,
                FEATURE_PRIORITY_INGEST,
            }
        ),
    ),
    "enterprise": Plan(
        id="enterprise",
        name="Enterprise",
        price_usd_month=0,  # custom / contact sales
        max_workspaces=-1,
        max_members=-1,
        monthly_query_limit=-1,
        monthly_upload_limit=-1,
        storage_mb=-1,
        features=frozenset(
            {
                FEATURE_API_ACCESS,
                FEATURE_EXTERNAL_DELEGATION,
                FEATURE_AUDIT_EXPORT,
                FEATURE_PRIORITY_INGEST,
                FEATURE_SSO,
            }
        ),
    ),
}

DEFAULT_PLAN_ID = "free"


def get_plan(plan_id: str | None) -> Plan:
    """Return the plan for ``plan_id``, falling back to the free plan."""
    if plan_id and plan_id in PLANS:
        return PLANS[plan_id]
    return PLANS[DEFAULT_PLAN_ID]


def feature_enabled(plan_id: str | None, feature: str) -> bool:
    return get_plan(plan_id).has_feature(feature)


def assert_within_limit(plan_id: str | None, metric: str, current: int, delta: int = 1) -> None:
    """Raise ``PlanLimitExceeded`` if ``current + delta`` would exceed the limit.

    A limit of ``-1`` means unlimited and never raises.
    """
    limit = get_plan(plan_id).limit_for(metric)
    if limit < 0:
        return
    if current + delta > limit:
        raise PlanLimitExceeded(metric, limit, current)
