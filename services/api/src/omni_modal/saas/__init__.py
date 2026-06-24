"""SaaS layer: organizations, workspaces, plans, usage metering, notifications.

Everything in this package works fully offline with in-memory stores so the
local demo keeps running with zero paid services. Paid integrations (Stripe
billing, S3 storage, Resend email, PostHog analytics) are exposed as optional
adapters in ``omni_modal.saas.adapters`` that default to local, no-op
implementations and only activate when their feature flag / credentials are set.
"""

from __future__ import annotations

from omni_modal.saas.plans import (
    PLANS,
    Plan,
    PlanLimitExceeded,
    feature_enabled,
    get_plan,
)
from omni_modal.saas.workspaces import (
    Invite,
    Membership,
    Organization,
    Workspace,
    WorkspaceStore,
)
from omni_modal.saas.usage import UsageStore
from omni_modal.saas.notifications import Notification, NotificationStore
from omni_modal.saas.service import SaasService, get_saas_service

__all__ = [
    "PLANS",
    "Plan",
    "PlanLimitExceeded",
    "feature_enabled",
    "get_plan",
    "Invite",
    "Membership",
    "Organization",
    "Workspace",
    "WorkspaceStore",
    "UsageStore",
    "Notification",
    "NotificationStore",
    "SaasService",
    "get_saas_service",
]
