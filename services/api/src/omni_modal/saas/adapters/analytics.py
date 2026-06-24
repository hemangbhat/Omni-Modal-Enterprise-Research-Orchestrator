"""Product analytics adapter: in-memory by default, PostHog if configured.

The in-memory adapter keeps the last N events so an admin dashboard can show
real event counts offline. The PostHog adapter posts events via stdlib urllib
and only activates when ``POSTHOG_API_KEY`` is set.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class AnalyticsEvent:
    event: str
    tenant_id: str
    user_id: str | None
    properties: dict[str, object]
    timestamp: float


class AnalyticsAdapter(Protocol):
    backend: str

    def capture(
        self, *, event: str, tenant_id: str, user_id: str | None = None,
        properties: dict[str, object] | None = None,
    ) -> None: ...


class InMemoryAnalyticsAdapter:
    """Bounded in-memory event log with aggregate counts for dashboards."""

    backend = "in-memory"

    def __init__(self, max_events: int = 1000) -> None:
        self._lock = threading.RLock()
        self._events: list[AnalyticsEvent] = []
        self._counts: Counter[str] = Counter()
        self._max = max_events

    def capture(
        self, *, event: str, tenant_id: str, user_id: str | None = None,
        properties: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            self._events.append(
                AnalyticsEvent(
                    event=event,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    properties=properties or {},
                    timestamp=time.time(),
                )
            )
            self._counts[event] += 1
            if len(self._events) > self._max:
                del self._events[: len(self._events) - self._max]

    def event_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def recent(self, limit: int = 50) -> list[AnalyticsEvent]:
        with self._lock:
            return list(self._events[-limit:])[::-1]


class PostHogAnalyticsAdapter:
    """Posts events to PostHog via stdlib urllib."""

    backend = "posthog"

    def __init__(self) -> None:
        self._api_key = os.environ["POSTHOG_API_KEY"]
        self._host = os.environ.get("POSTHOG_HOST", "https://app.posthog.com").rstrip("/")

    def capture(
        self, *, event: str, tenant_id: str, user_id: str | None = None,
        properties: dict[str, object] | None = None,
    ) -> None:
        import urllib.request  # noqa: PLC0415

        payload = json.dumps(
            {
                "api_key": self._api_key,
                "event": event,
                "distinct_id": user_id or tenant_id,
                "properties": {"tenant_id": tenant_id, **(properties or {})},
            }
        ).encode()
        req = urllib.request.Request(
            f"{self._host}/capture/",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5):  # noqa: S310
                pass
        except Exception as exc:  # pragma: no cover - network
            print(f"[analytics] PostHog capture failed: {exc}", file=sys.stderr)


def select_analytics_adapter() -> AnalyticsAdapter:
    if os.environ.get("POSTHOG_API_KEY"):
        try:
            return PostHogAnalyticsAdapter()
        except Exception as exc:  # pragma: no cover
            print(
                f"[analytics] POSTHOG_API_KEY set but adapter unavailable ({exc}); "
                f"falling back to in-memory analytics.",
                file=sys.stderr,
            )
    return InMemoryAnalyticsAdapter()
