"""Property 14: Workflow total timeout is enforced.
Feature: performance-and-scalability, Property 14
Validates: Requirements 7.3
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

import _path
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.orchestration.adk_workflow import DeterministicAgentGraph


def _make_slow_node(sleep_seconds: float) -> MagicMock:
    node = MagicMock()
    node.__class__.__name__ = "SlowNode"
    def run(state):
        time.sleep(sleep_seconds)
        state.response = MagicMock()
        return state
    node.run.side_effect = run
    return node


class TestWorkflowTotalTimeoutEnforced(unittest.TestCase):
    """Property 14: Workflow total timeout is enforced — Validates: Requirements 7.3"""

    @given(
        total_ms=st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=10, deadline=10_000)  # reduced examples due to real sleep
    def test_timeout_enforced_when_exceeded(self, total_ms: float) -> None:
        """Property 14: DeterministicAgentGraph raises TimeoutError before completing."""
        total_timeout_ms = total_ms
        sleep_seconds = (total_ms / 1000) + 0.05  # sleep longer than the timeout

        slow_node = _make_slow_node(sleep_seconds)
        graph = DeterministicAgentGraph([slow_node], total_timeout_ms=total_timeout_ms)

        from omni_modal.qa.models import QueryRequest
        request = QueryRequest(
            tenant_id="t", user_id="u", question="q",
            top_k=5, min_similarity=0.0, stream=False
        )
        mock_state = MagicMock()
        mock_state.response = None

        with patch("omni_modal.orchestration.adk_workflow.AgentGraphState", return_value=mock_state):
            with self.assertRaises(TimeoutError):
                graph.run(request)

    def test_timeout_not_raised_when_within_budget(self):
        """Workflow completes normally when steps finish within total_timeout_ms."""
        response = MagicMock()
        def fast_run(state):
            state.response = response
            return state

        fast_node = MagicMock()
        fast_node.__class__.__name__ = "FastNode"
        fast_node.run.side_effect = fast_run

        graph = DeterministicAgentGraph([fast_node], total_timeout_ms=10_000.0)

        from omni_modal.qa.models import QueryRequest
        request = QueryRequest(
            tenant_id="t", user_id="u", question="q",
            top_k=5, min_similarity=0.0, stream=False
        )
        mock_state = MagicMock()
        mock_state.response = None

        with patch("omni_modal.orchestration.adk_workflow.AgentGraphState", return_value=mock_state):
            with patch("omni_modal.orchestration.adk_workflow.observability"):
                result = graph.run(request)
        # Should not raise
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
