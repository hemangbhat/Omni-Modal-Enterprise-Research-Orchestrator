"""Minimal, dependency-free metrics registry with Prometheus text exposition.

Phase 4 (observability + SLOs). Sentry already provides error + trace data; this
adds the metrics pillar — request counts and latency histograms — without
pulling in the full ``prometheus_client`` dependency. The output is valid
Prometheus exposition format, so a real Prometheus/Grafana stack can scrape
``GET /metrics`` directly in production.

Thread-safe; all state is in-process (per web instance), which is the standard
model for Prometheus (it scrapes each instance and aggregates centrally).
"""

from __future__ import annotations

import threading
from typing import Iterable

# SLO-oriented latency buckets (seconds). The last (+Inf) bucket is implicit.
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

_LabelKey = tuple[tuple[str, str], ...]


def _label_key(labels: dict[str, str]) -> _LabelKey:
    return tuple(sorted(labels.items()))


def _format_labels(key: _LabelKey, extra: dict[str, str] | None = None) -> str:
    items = dict(key)
    if extra:
        items.update(extra)
    if not items:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(items.items()))
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    """In-process counters + latency histograms with Prometheus exposition."""

    def __init__(self, buckets: Iterable[float] = _DEFAULT_BUCKETS) -> None:
        self._lock = threading.Lock()
        self._buckets = tuple(sorted(buckets))
        self._counters: dict[str, dict[_LabelKey, float]] = {}
        # histogram name -> label_key -> {"buckets": [...], "sum": float, "count": int}
        self._histograms: dict[str, dict[_LabelKey, dict]] = {}

    def inc_counter(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = _label_key(labels or {})
        with self._lock:
            series = self._counters.setdefault(name, {})
            series[key] = series.get(key, 0.0) + value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels or {})
        with self._lock:
            series = self._histograms.setdefault(name, {})
            entry = series.get(key)
            if entry is None:
                entry = {"buckets": [0] * len(self._buckets), "sum": 0.0, "count": 0}
                series[key] = entry
            entry["sum"] += value
            entry["count"] += 1
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    entry["buckets"][i] += 1

    def render(self) -> str:
        """Return all metrics in Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            for name, series in self._counters.items():
                lines.append(f"# TYPE {name} counter")
                for key, val in series.items():
                    lines.append(f"{name}{_format_labels(key)} {_fmt(val)}")

            for name, series in self._histograms.items():
                lines.append(f"# TYPE {name} histogram")
                for key, entry in series.items():
                    # entry["buckets"][i] already holds the cumulative count of
                    # observations <= bucket bound (le semantics).
                    for i, bound in enumerate(self._buckets):
                        le = repr(bound) if bound != int(bound) else str(int(bound))
                        lines.append(
                            f"{name}_bucket{_format_labels(key, {'le': le})} {entry['buckets'][i]}"
                        )
                    lines.append(
                        f"{name}_bucket{_format_labels(key, {'le': '+Inf'})} {entry['count']}"
                    )
                    lines.append(f"{name}_sum{_format_labels(key)} {_fmt(entry['sum'])}")
                    lines.append(f"{name}_count{_format_labels(key)} {entry['count']}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


def _fmt(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(value)


# Process-wide registry.
metrics = Metrics()
