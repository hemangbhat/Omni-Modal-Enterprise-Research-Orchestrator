"""Optional integration adapters with local-first defaults.

Each adapter ships a fully working local implementation and an optional
"real" implementation that only activates when the relevant credentials are
present. This keeps the zero-paid-services promise: nothing here requires an
external account to run, but the upgrade path is real, not faked.

- Storage : local filesystem (default) | S3-compatible (if S3_BUCKET set)
- Email   : console/log (default)      | Resend (if RESEND_API_KEY set)
- Analytics: in-memory (default)        | PostHog (if POSTHOG_API_KEY set)
"""

from __future__ import annotations

from omni_modal.saas.adapters.storage import (
    LocalStorageAdapter,
    StorageAdapter,
    select_storage_adapter,
)
from omni_modal.saas.adapters.email import (
    ConsoleEmailAdapter,
    EmailAdapter,
    select_email_adapter,
)
from omni_modal.saas.adapters.analytics import (
    AnalyticsAdapter,
    InMemoryAnalyticsAdapter,
    select_analytics_adapter,
)
from omni_modal.saas.adapters.billing import (
    DemoBillingAdapter,
    StripeBillingAdapter,
    select_billing_adapter,
)

__all__ = [
    "StorageAdapter",
    "LocalStorageAdapter",
    "select_storage_adapter",
    "EmailAdapter",
    "ConsoleEmailAdapter",
    "select_email_adapter",
    "AnalyticsAdapter",
    "InMemoryAnalyticsAdapter",
    "select_analytics_adapter",
    "DemoBillingAdapter",
    "StripeBillingAdapter",
    "select_billing_adapter",
]
