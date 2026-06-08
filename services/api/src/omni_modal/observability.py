from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Iterator, Any, Callable, Mapping
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# PII pattern definitions (module-level, compiled once)
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),          # email
    re.compile(r"[a-zA-Z]+://[^\s]+"),                                         # URLs / connection strings
    re.compile(r"(?:password|secret|token|key)\s*[:=]\s*\S+", re.IGNORECASE), # secret key-value pairs
]

# Default ports that should be omitted from the host representation
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

def scrub_value(value: str) -> str:
    """Replace PII patterns in a string with '<redacted>'.

    Handles email addresses, URL/connection strings, and secret key-value
    pairs (password, secret, token, key).
    """
    for pattern in _PII_PATTERNS:
        value = pattern.sub("<redacted>", value)
    return value


def scrub_pii(data: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with all string values scrubbed of PII.

    Recurses into nested dicts.  Non-string, non-dict values are left
    unchanged (shallow copy).
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = scrub_value(value)
        elif isinstance(value, dict):
            result[key] = scrub_pii(value)
        else:
            result[key] = value
    return result


def extract_host(url: str) -> str:
    """Return only the host (and port if non-default) from a URL string.

    Strips scheme, path, query parameters, and fragment.  For the default
    ports (80 for http, 443 for https), only the hostname is returned.

    Examples:
        extract_host("https://example.com/api/v1?q=1") -> "example.com"
        extract_host("http://host:8080/path")           -> "host:8080"
        extract_host("http://host:80/path")             -> "host"
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    port = parsed.port
    scheme = parsed.scheme.lower()

    default_port = _DEFAULT_PORTS.get(scheme)
    if port is None or port == default_port:
        return hostname
    return f"{hostname}:{port}"


# ---------------------------------------------------------------------------
# Observability facade
# ---------------------------------------------------------------------------

class Observability:
    def __init__(self) -> None:
        self._sentry = None
        self._initialized = False
        # Optional before_send hook to scrub PII
        self._before_send: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def init(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        dsn = os.environ.get("SENTRY_DSN")
        if not dsn:
            return
        try:
            import sentry_sdk  # type: ignore[import-not-found]
        except ImportError:
            return

        def _before_send(event: dict[str, Any]) -> dict[str, Any]:
            # Scrub extra dict
            if "extra" in event and isinstance(event["extra"], dict):
                event["extra"] = scrub_pii(event["extra"])

            # Scrub breadcrumb messages
            breadcrumbs = event.get("breadcrumbs", {})
            if isinstance(breadcrumbs, dict):
                values = breadcrumbs.get("values", [])
                if isinstance(values, list):
                    for crumb in values:
                        if isinstance(crumb, dict) and isinstance(crumb.get("message"), str):
                            crumb["message"] = scrub_value(crumb["message"])

            # Scrub string tag values
            if "tags" in event and isinstance(event["tags"], dict):
                event["tags"] = {
                    k: scrub_value(v) if isinstance(v, str) else v
                    for k, v in event["tags"].items()
                }

            return event

        self._before_send = _before_send

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("ENVIRONMENT", "development"),
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            before_send=_before_send,
        )
        self._sentry = sentry_sdk

    def capture_exception(
        self,
        error: BaseException,
        *,
        operation: str,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self.init()
        if self._sentry is None:
            return
        with self._sentry.push_scope() as scope:
            scope.set_tag("operation", operation)
            for key, value in (context or {}).items():
                scope.set_context(key, {"value": _safe_value(value)})
            self._sentry.capture_exception(error)

    def capture_message(
        self,
        message: str,
        *,
        operation: str,
        context: Mapping[str, Any] | None = None,
        level: str = "warning",
    ) -> None:
        self.init()
        if self._sentry is None:
            return
        with self._sentry.push_scope() as scope:
            scope.set_tag("operation", operation)
            for key, value in (context or {}).items():
                scope.set_context(key, {"value": _safe_value(value)})
            self._sentry.capture_message(message, level=level)

    def add_breadcrumb(
        self,
        *,
        message: str,
        category: str = "default",
        level: str = "info",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """Add a Sentry breadcrumb if SDK is initialized."""
        self.init()
        if self._sentry is None:
            return
        self._sentry.add_breadcrumb(
            message=message,
            category=category,
            level=level,
            data=data or {},
        )

    def set_user(self, user_id: str, email: str | None = None) -> None:
        """Attach user information to future events."""
        self.init()
        if self._sentry is None:
            return
        with self._sentry.configure_scope() as scope:
            scope.set_user({"id": user_id, "email": email})

    def set_tag(self, key: str, value: str) -> None:
        self.init()
        if self._sentry is None:
            return
        with self._sentry.configure_scope() as scope:
            scope.set_tag(key, value)

    @contextmanager
    def span(self, operation: str, description: str) -> Iterator[None]:
        self.init()
        if self._sentry is None:
            yield
            return
        with self._sentry.start_transaction(op=operation, name=description):
            yield

    @contextmanager
    def continue_trace(self, headers: dict[str, str]) -> Iterator[None]:
        """Start or continue a distributed trace from sentry-trace/baggage headers.

        If headers are absent or malformed, starts a new root transaction
        without raising.  If the SDK is unavailable, yields without error.
        """
        self.init()
        if self._sentry is None:
            yield
            return

        trace_header = headers.get("sentry-trace", "")
        baggage_header = headers.get("baggage", "")

        try:
            if trace_header:
                # continue_trace returns a context manager wrapping a transaction
                ctx = self._sentry.continue_trace(
                    {"sentry-trace": trace_header, "baggage": baggage_header},
                    op="http.server",
                    name="request",
                )
                with ctx:
                    yield
            else:
                # No incoming trace header — start a fresh root transaction
                with self._sentry.start_transaction(op="http.server", name="request"):
                    yield
        except Exception:
            # Malformed headers or any SDK error — fall back to a new root transaction
            try:
                with self._sentry.start_transaction(op="http.server", name="request"):
                    yield
            except Exception:
                yield

    @contextmanager
    def child_span(self, operation: str, description: str) -> Iterator[None]:
        """Create a child span nested under the current active transaction.

        If the SDK is unavailable, yields without error.
        """
        self.init()
        if self._sentry is None:
            yield
            return
        with self._sentry.start_span(op=operation, description=description):
            yield

    def set_request_scope(self, tenant_id: str, user_id: str | None = None) -> None:
        """Set tenant_id and (optionally) user_id as Sentry scope tags.

        If the SDK is unavailable, does nothing without error.
        """
        self.init()
        if self._sentry is None:
            return
        self._sentry.set_tag("tenant_id", tenant_id)
        if user_id is not None:
            self._sentry.set_tag("user_id", user_id)

    def flush(self, timeout: float = 2.0) -> None:
        """Force Sentry to send any pending events before shutdown."""
        self.init()
        if self._sentry is None:
            return
        self._sentry.flush(timeout)


def _safe_value(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = str(value)
        if "://" in text or "password" in text.lower() or "secret" in text.lower():
            return "<redacted>"
        return value
    return "<redacted>"


observability = Observability()
