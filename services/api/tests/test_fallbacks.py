"""Tests for orchestration/fallbacks.py.

Covers:
  - Property 11: Fallback Warning Aggregation (task 3.1)
  - Property 12: Transcription Failure Message Completeness (task 3.2)
  - Unit tests for FallbackController (task 3.3)

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.6**
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import _path  # noqa: F401  — adds services/api/src to sys.path
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.orchestration.fallbacks import (
    FallbackController,
    FallbackWarning,
    OrchestrationResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SUBSYSTEMS = ["external_delegation", "retrieval", "transcription"]

_PATCH_TARGET = "omni_modal.observability.observability.capture_exception"


# ---------------------------------------------------------------------------
# Property 11: Fallback Warning Aggregation
# **Validates: Requirements 10.4, 10.6**
# ---------------------------------------------------------------------------

class TestFallbackWarningAggregation(unittest.TestCase):
    """Property 11: Fallback Warning Aggregation
    **Validates: Requirements 10.4, 10.6**
    """

    @given(
        failing=st.lists(st.sampled_from(SUBSYSTEMS), min_size=0, max_size=3, unique=True),
        failing_tools=st.lists(
            st.text(min_size=1, max_size=20), min_size=0, max_size=3, unique=True
        ),
    )
    @settings(max_examples=100)
    def test_fallback_warning_aggregation(self, failing, failing_tools):
        """One warning per failed subsystem; one skipped_tools entry per failed tool."""
        with patch(_PATCH_TARGET):
            ctrl = FallbackController()
            warnings = []
            skipped_tools = []

            for source in failing:
                exc = RuntimeError(f"{source} error")
                if source == "external_delegation":
                    w = ctrl.handle_delegation_failure(exc, "req-123")
                elif source == "retrieval":
                    w = ctrl.handle_retrieval_failure(exc, "query text")
                else:  # transcription
                    w = ctrl.handle_transcription_failure(exc, "decode")
                warnings.append(w)

            for tool_name in failing_tools:
                exc = RuntimeError(f"{tool_name} error")
                entry = ctrl.handle_tool_failure(exc, tool_name)
                skipped_tools.append(entry)

            result = OrchestrationResult(
                response={},
                warnings=warnings,
                skipped_tools=skipped_tools,
                partial=bool(failing or failing_tools),
            )

            # Assert one warning per failed subsystem with correct source
            sources = [w.source for w in result.warnings]
            self.assertEqual(len(sources), len(failing))
            for source in failing:
                self.assertIn(source, sources)

            # Assert one skipped_tools entry per failed tool
            self.assertEqual(len(result.skipped_tools), len(failing_tools))
            for tool_name in failing_tools:
                self.assertTrue(
                    any(e["name"] == tool_name for e in result.skipped_tools)
                )


# ---------------------------------------------------------------------------
# Property 12: Transcription Failure Message Completeness
# **Validates: Requirements 4.2, 10.2**
# ---------------------------------------------------------------------------

class TestTranscriptionFailureMessageCompleteness(unittest.TestCase):
    """Property 12: Transcription Failure Message Completeness
    **Validates: Requirements 4.2, 10.2**
    """

    @given(
        stage=st.sampled_from(["model_load", "audio_decode", "transcription"]),
        exc_class=st.sampled_from([RuntimeError, ValueError, TimeoutError, OSError]),
    )
    @settings(max_examples=100)
    def test_transcription_failure_message_completeness(self, stage, exc_class):
        """The warning reason must contain both the stage name and exception type name."""
        with patch(_PATCH_TARGET):
            ctrl = FallbackController()
            exc = exc_class("some error detail")
            warning = ctrl.handle_transcription_failure(exc, stage)
            self.assertIn(stage, warning.reason)
            self.assertIn(type(exc).__name__, warning.reason)


# ---------------------------------------------------------------------------
# Unit Tests (task 3.3)
# **Validates: Requirements 10.1, 10.3, 10.4, 10.6**
# ---------------------------------------------------------------------------

class TestFallbackControllerUnit(unittest.TestCase):
    """Unit tests for FallbackController."""

    def test_delegation_fallback_source(self):
        """handle_delegation_failure returns FallbackWarning with source='external_delegation'."""
        with patch(_PATCH_TARGET):
            ctrl = FallbackController()
            exc = ConnectionError("remote host refused connection")
            warning = ctrl.handle_delegation_failure(exc, "req-abc")

        self.assertIsInstance(warning, FallbackWarning)
        self.assertEqual(warning.source, "external_delegation")
        self.assertEqual(warning.exception_type, "ConnectionError")
        self.assertIn("remote host refused connection", warning.reason)

    def test_retrieval_fallback_source(self):
        """handle_retrieval_failure returns FallbackWarning with source='retrieval'."""
        with patch(_PATCH_TARGET):
            ctrl = FallbackController()
            exc = TimeoutError("db timed out")
            warning = ctrl.handle_retrieval_failure(exc, "search query")

        self.assertIsInstance(warning, FallbackWarning)
        self.assertEqual(warning.source, "retrieval")
        self.assertEqual(warning.exception_type, "TimeoutError")
        self.assertIn("db timed out", warning.reason)

    def test_tool_failure_returns_dict(self):
        """handle_tool_failure returns dict with 'name' and 'reason' keys."""
        with patch(_PATCH_TARGET):
            ctrl = FallbackController()
            exc = RuntimeError("tool unavailable")
            entry = ctrl.handle_tool_failure(exc, "search_web")

        self.assertIsInstance(entry, dict)
        self.assertIn("name", entry)
        self.assertIn("reason", entry)
        self.assertEqual(entry["name"], "search_web")
        self.assertEqual(entry["reason"], "tool unavailable")

    def test_multiple_warnings_aggregated(self):
        """Building multiple warnings results in all of them appearing in OrchestrationResult.warnings."""
        with patch(_PATCH_TARGET):
            ctrl = FallbackController()

            w1 = ctrl.handle_delegation_failure(ConnectionError("net err"), "req-1")
            w2 = ctrl.handle_retrieval_failure(TimeoutError("slow db"), "some query")
            w3 = ctrl.handle_transcription_failure(ValueError("bad audio"), "audio_decode")

        result = OrchestrationResult(
            response={"answer": "partial"},
            warnings=[w1, w2, w3],
            skipped_tools=[],
            partial=True,
        )

        sources = {w.source for w in result.warnings}
        self.assertEqual(len(result.warnings), 3)
        self.assertIn("external_delegation", sources)
        self.assertIn("retrieval", sources)
        self.assertIn("transcription", sources)
        self.assertTrue(result.partial)


if __name__ == "__main__":
    unittest.main()
