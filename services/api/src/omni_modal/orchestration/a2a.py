from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Protocol
from uuid import uuid4
from omni_modal.observability import observability, extract_host
from omni_modal.retry import retry_with_backoff, is_retryable


@dataclass(frozen=True)
class ExternalResearchFinding:
    claim: str
    source_title: str
    source_url: str | None = None
    confidence: float | None = None
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class A2AResearchRequest:
    request_id: str
    tenant_id: str
    user_id: str
    question: str
    reason: str
    internal_status: str

    def to_message(self) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": "research.answer_external",
            "params": {
                "question": self.question,
                "reason": self.reason,
                "internal_status": self.internal_status,
                "provenance_policy": "Return external findings only. Do not infer internal facts.",
                "response_schema": {
                    "findings": [
                        {
                            "claim": "string",
                            "source_title": "string",
                            "source_url": "string|null",
                            "confidence": "number|null",
                        }
                    ],
                    "summary": "string",
                },
            },
            "metadata": {
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "contains_internal_content": False,
            },
        }


@dataclass(frozen=True)
class A2AResearchResponse:
    request_id: str
    status: str
    findings: list[ExternalResearchFinding]
    summary: str | None = None
    error_message: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "findings": [asdict(finding) for finding in self.findings],
            "summary": self.summary,
            "error_message": self.error_message,
        }


class ExternalResearchClient(Protocol):
    def delegate(self, request: A2AResearchRequest) -> A2AResearchResponse:
        raise NotImplementedError


class DisabledExternalResearchClient:
    def delegate(self, request: A2AResearchRequest) -> A2AResearchResponse:
        return A2AResearchResponse(
            request_id=request.request_id,
            status="disabled",
            findings=[],
            error_message="External delegation is not configured.",
        )


class HttpA2AResearchClient:
    def __init__(self, endpoint: str, timeout_seconds: float = 20.0) -> None:
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds

    def delegate(self, request: A2AResearchRequest) -> A2AResearchResponse:
        # Task 11.6: Redact content before delegation to prevent internal data leakage
        try:
            from omni_modal.security.redactor import redact_request, ContentLeakError  # noqa: PLC0415
            try:
                request = redact_request(request, [])  # chunk_texts not available here; truncates internal_status
            except ContentLeakError as exc:
                observability.capture_exception(exc, operation="a2a.redaction")
                return A2AResearchResponse(
                    request_id=request.request_id,
                    status="redacted",
                    findings=[],
                    error_message="Delegation aborted: internal content detected in payload.",
                )
        except ImportError:
            pass  # redactor module not available; skip redaction

        endpoint_host = extract_host(self._endpoint)

        # Breadcrumb: request started
        observability.add_breadcrumb(
            message="External delegation request started",
            category="delegation",
            level="info",
            data={"endpoint_host": endpoint_host, "request_id": request.request_id},
        )

        payload = json.dumps(request.to_message()).encode("utf-8")
        http_request = urllib.request.Request(
            self._endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        start_time = time.monotonic()

        @retry_with_backoff(
            max_retries=3,
            base_delay=1.0,
            retryable_exceptions=(urllib.error.URLError, TimeoutError, ConnectionError),
            retryable=is_retryable,
            respect_retry_after=True,
        )
        def _do_request() -> str:
            with urllib.request.urlopen(http_request, timeout=self._timeout_seconds) as resp:
                return resp.read().decode("utf-8")

        try:
            raw = _do_request()
        except urllib.error.HTTPError as exc:
            response_body = ""
            try:
                response_body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            observability.capture_exception(
                exc,
                operation="a2a.delegate",
                context={
                    "endpoint_host": endpoint_host,
                    "request_id": request.request_id,
                    "http_status": exc.code,
                    "response_preview": response_body,
                    "error_type": "http_error",
                },
            )
            return A2AResearchResponse(
                request_id=request.request_id,
                status="failed",
                findings=[],
                error_message=f"HTTP {exc.code}: {response_body[:200]}",
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            error_type = "timeout" if isinstance(exc, TimeoutError) else "connection_refused"
            if isinstance(exc, urllib.error.URLError):
                reason = str(exc.reason)
                if "connection refused" in reason.lower():
                    error_type = "connection_refused"
                elif "name or service not known" in reason.lower() or "nodename nor servname" in reason.lower():
                    error_type = "dns_failure"
                else:
                    error_type = "connection_reset"
            observability.capture_exception(
                exc,
                operation="a2a.delegate",
                context={
                    "endpoint_host": endpoint_host,
                    "request_id": request.request_id,
                    "error_type": error_type,
                },
            )
            return A2AResearchResponse(
                request_id=request.request_id,
                status="failed",
                findings=[],
                error_message=str(exc),
            )

        # Breadcrumb: response received
        observability.add_breadcrumb(
            message="External delegation response received",
            category="delegation",
            level="info",
            data={
                "http_status": 200,
                "response_time_ms": int((time.monotonic() - start_time) * 1000),
            },
        )

        return parse_a2a_response(request.request_id, raw)


def external_client_from_environment() -> ExternalResearchClient:
    endpoint = os.environ.get("A2A_DELEGATION_ENDPOINT") or os.environ.get(
        "GEMINI_INTERACTIONS_ENDPOINT"
    )
    if not endpoint:
        return DisabledExternalResearchClient()
    return HttpA2AResearchClient(endpoint)


def build_a2a_request(
    tenant_id: str,
    user_id: str,
    question: str,
    reason: str,
    internal_status: str,
) -> A2AResearchRequest:
    return A2AResearchRequest(
        request_id=str(uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        question=question,
        reason=reason,
        internal_status=internal_status,
    )


def parse_a2a_response(request_id: str, raw: str) -> A2AResearchResponse:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        observability.capture_exception(
            exc,
            operation="a2a.parse_response",
            context={
                "request_id": request_id,
                "response_preview": raw[:500],
                "error_type": "parse_error",
            },
        )
        return A2AResearchResponse(
            request_id=request_id,
            status="failed",
            findings=[],
            error_message=f"Invalid A2A JSON response: {exc}",
        )

    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        return A2AResearchResponse(
            request_id=request_id,
            status="failed",
            findings=[],
            error_message="A2A response result must be an object.",
        )

    raw_findings = result.get("findings", [])
    if not isinstance(raw_findings, list):
        return A2AResearchResponse(
            request_id=request_id,
            status="failed",
            findings=[],
            error_message="A2A findings must be a list.",
        )

    findings: list[ExternalResearchFinding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        claim = item.get("claim")
        source_title = item.get("source_title")
        if not isinstance(claim, str) or not claim.strip():
            continue
        if not isinstance(source_title, str) or not source_title.strip():
            continue
        source_url = item.get("source_url")
        confidence = item.get("confidence")
        findings.append(
            ExternalResearchFinding(
                claim=claim.strip(),
                source_title=source_title.strip(),
                source_url=source_url if isinstance(source_url, str) else None,
                confidence=float(confidence)
                if isinstance(confidence, (int, float))
                else None,
            )
        )

    return A2AResearchResponse(
        request_id=request_id,
        status="ok" if findings else "no_data",
        findings=findings,
        summary=result.get("summary") if isinstance(result.get("summary"), str) else None,
    )
