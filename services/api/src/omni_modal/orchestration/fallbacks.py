from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FallbackWarning:
    source: str           # e.g. "external_delegation", "retrieval", "transcription"
    reason: str           # human-readable failure reason
    exception_type: str   # type(exc).__name__


@dataclass
class OrchestrationResult:
    response: Any                                    # ResearchResponse or dict
    warnings: list[FallbackWarning] = field(default_factory=list)
    skipped_tools: list[dict[str, str]] = field(default_factory=list)
    partial: bool = False


class FallbackController:
    def handle_delegation_failure(self, exc: BaseException, request_id: str) -> FallbackWarning:
        """Return warning for failed external delegation; capture to Sentry."""
        from omni_modal.observability import observability
        observability.capture_exception(
            exc,
            operation="external_delegation",
            context={"request_id": request_id, "error_type": type(exc).__name__},
        )
        return FallbackWarning(
            source="external_delegation",
            reason=str(exc),
            exception_type=type(exc).__name__,
        )

    def handle_retrieval_failure(self, exc: BaseException, query: str) -> FallbackWarning:
        """Return warning for failed retrieval."""
        from omni_modal.observability import observability
        observability.capture_exception(
            exc,
            operation="retrieval",
            context={"query_length": len(query), "error_type": type(exc).__name__},
        )
        return FallbackWarning(
            source="retrieval",
            reason=str(exc),
            exception_type=type(exc).__name__,
        )

    def handle_transcription_failure(self, exc: BaseException, stage: str) -> FallbackWarning:
        """Return warning; builds error message with stage name and exception type."""
        from omni_modal.observability import observability
        error_message = f"Transcription failed at stage '{stage}': {type(exc).__name__}: {exc}"
        observability.capture_exception(
            exc,
            operation="transcription",
            context={"stage": stage, "error_type": type(exc).__name__},
        )
        return FallbackWarning(
            source="transcription",
            reason=error_message,
            exception_type=type(exc).__name__,
        )

    def handle_tool_failure(self, exc: BaseException, tool_name: str) -> dict[str, str]:
        """Return skipped tool entry for failed MCP tool call."""
        from omni_modal.observability import observability
        observability.capture_exception(
            exc,
            operation="tool_call",
            context={"tool_name": tool_name, "error_type": type(exc).__name__},
        )
        return {"name": tool_name, "reason": str(exc)}
