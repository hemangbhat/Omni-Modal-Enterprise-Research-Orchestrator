"""Per-tenant usage metering with monthly billing periods.

In-process and thread-safe. Counters are keyed by (tenant_id, period, metric)
where ``period`` is a ``YYYY-MM`` string so usage naturally resets each month.
This is the real source of truth for plan-limit enforcement on the demo/local
path. For multi-node production this would be backed by Redis/Postgres, but the
interface stays identical.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


def current_period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


class UsageStore:
    """Thread-safe monthly usage counters."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # tenant_id -> period -> metric -> count
        self._counts: dict[str, dict[str, dict[str, int]]] = {}

    def record(self, tenant_id: str, metric: str, amount: int = 1, *, period: str | None = None) -> int:
        if amount < 0:
            raise ValueError("usage amount must be non-negative")
        period = period or current_period()
        with self._lock:
            tenant = self._counts.setdefault(tenant_id, {})
            metrics = tenant.setdefault(period, {})
            metrics[metric] = metrics.get(metric, 0) + amount
            return metrics[metric]

    def get(self, tenant_id: str, metric: str, *, period: str | None = None) -> int:
        period = period or current_period()
        with self._lock:
            return (
                self._counts.get(tenant_id, {}).get(period, {}).get(metric, 0)
            )

    def snapshot(self, tenant_id: str, *, period: str | None = None) -> dict[str, int]:
        period = period or current_period()
        with self._lock:
            return dict(self._counts.get(tenant_id, {}).get(period, {}))

    def all_tenants(self) -> list[str]:
        with self._lock:
            return list(self._counts.keys())
