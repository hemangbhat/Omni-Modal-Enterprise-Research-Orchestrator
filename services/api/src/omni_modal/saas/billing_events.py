"""A log of applied Stripe events, so a replayed or late event cannot undo newer state.

Stripe delivers events at least once and not in order. Two rules follow:
an event id already applied is skipped, and a plan-changing event created
before the last plan event applied to the same organization is skipped
(for example a delayed "checkout completed" arriving after a cancellation).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

PLAN_EVENTS = frozenset({
    "checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted",
})


@dataclass(frozen=True)
class AppliedEvent:
    event_id: str
    event_type: str
    tenant_id: str | None
    created: float | None


class BillingEventStore(Protocol):
    def seen(self, event_id: str) -> bool: ...

    def last_plan_event_at(self, tenant_id: str) -> float | None: ...

    def record(self, event: AppliedEvent) -> bool:
        """Store the event; False when its id was already stored (a concurrent delivery)."""
        ...


class InMemoryBillingEventStore:
    def __init__(self) -> None:
        self._events: dict[str, AppliedEvent] = {}
        self._lock = threading.Lock()

    def seen(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._events

    def last_plan_event_at(self, tenant_id: str) -> float | None:
        with self._lock:
            times = [e.created for e in self._events.values()
                     if e.tenant_id == tenant_id and e.event_type in PLAN_EVENTS and e.created is not None]
            return max(times) if times else None

    def record(self, event: AppliedEvent) -> bool:
        with self._lock:
            if event.event_id in self._events:
                return False
            self._events[event.event_id] = event
            return True


def _ts(epoch: float | None):
    return None if epoch is None else datetime.fromtimestamp(epoch, tz=timezone.utc)


class PostgresBillingEventStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    def seen(self, event_id: str) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM stripe_events WHERE event_id = %s", (event_id,))
            return cur.fetchone() is not None

    def last_plan_event_at(self, tenant_id: str) -> float | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT max(stripe_created_at) FROM stripe_events WHERE tenant_id = %s AND event_type = ANY(%s)",
                (tenant_id, sorted(PLAN_EVENTS)),
            )
            row = cur.fetchone()
            return row[0].timestamp() if row and row[0] is not None else None

    def record(self, event: AppliedEvent) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO stripe_events (event_id, event_type, tenant_id, stripe_created_at)
                   VALUES (%s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""",
                (event.event_id, event.event_type, event.tenant_id, _ts(event.created)),
            )
            return cur.rowcount == 1


def select_billing_event_store() -> BillingEventStore:
    from omni_modal.saas.repository import _pool_or_none  # noqa: PLC0415

    pool = _pool_or_none()
    return PostgresBillingEventStore(pool) if pool is not None else InMemoryBillingEventStore()
