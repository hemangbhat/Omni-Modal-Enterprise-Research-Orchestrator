"""Request observability middleware for the FastAPI app (Phase 4).

Adds the three observability pillars that complement the existing Sentry
tracing:

  * **Correlation IDs** — every request gets an ``X-Correlation-ID`` (honoured
    from the inbound header if the frontend already set one, else generated).
    It is echoed on the response and included in logs so a single request can
    be traced end-to-end across the frontend and backend.
  * **Structured access logs** — one JSON line per request with method, route
    template, status, duration, and correlation id. Route *templates*
    (``/ingest/jobs/{job_id}``) are logged rather than concrete paths to avoid
    unbounded log/metric cardinality.
  * **Metrics** — request counts and a latency histogram recorded into the
    in-process Prometheus registry, exposed at ``GET /metrics``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import FastAPI, Request, Response

from omni_modal.metrics import metrics

logger = logging.getLogger("omero.access")

CORRELATION_HEADER = "X-Correlation-ID"


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or request.url.path


def install_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def _observe(request: Request, call_next):
        correlation_id = request.headers.get(CORRELATION_HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        start = time.perf_counter()
        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[CORRELATION_HEADER] = correlation_id
            return response
        finally:
            duration = time.perf_counter() - start
            route = _route_template(request)
            method = request.method
            labels = {"method": method, "route": route, "status": str(status_code)}
            try:
                metrics.inc_counter("http_requests_total", labels)
                metrics.observe(
                    "http_request_duration_seconds",
                    duration,
                    {"method": method, "route": route},
                )
            except Exception:  # noqa: BLE001 - metrics must never break a request
                pass
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "method": method,
                        "route": route,
                        "status": status_code,
                        "duration_ms": round(duration * 1000, 2),
                        "correlation_id": correlation_id,
                    }
                )
            )
