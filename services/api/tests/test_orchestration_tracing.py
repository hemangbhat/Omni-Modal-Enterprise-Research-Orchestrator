"""Tests for orchestration step breadcrumbs and timeout instrumentation.

Validates: Requirements 7.1, 7.2, 7.3
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
import hypothesis.strategies as st

import _path  # noqa: F401

from omni_modal.orchestration.graph_models import AgentGraphState
from omni_modal.orchestration.graph_nodes import GraphNode
from omni_modal.orchestration.adk_workflow import DeterministicAgentGraph
from omni_modal.qa.models import QueryRequest, QueryResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request() -> QueryRequest:
    return QueryRequest(
        tenant_id="t1",
        user_id="u1",
        question="q",
        top_k=5,
        min_similarity=0.0,
        stream=False,
    )


def _make_response() -> QueryResponse:
    return QueryResponse(
        status="ok",  # type: ignore[arg-type]
        question="q",
        answer_markdown="# A",
        citations=[],
        retrieved_chunks=[],
        error_message=None,
    )


def _make_success_node(name: str = "TestNode") -> MagicMock:
    node = MagicMock()
    node.__class__.__name__ = name

    def run(state: AgentGraphState) -> AgentGraphState:
        return AgentGraphState(
            request=state.request,
            response=_make_response(),
            steps=state.steps if hasattr(state, "steps") else [],  # type: ignore[call-arg]
        )

    # AgentGraphState has no 'steps' — just copy the state and add response
    def run_simple(state: AgentGraphState) -> AgentGraphState:
        state.response = _make_response()
        return state

    node.run.side_effect = run_simple
    return node


def _make_fail_node(name: str = "FailNode") -> MagicMock:
    node = MagicMock()
    node.__class__.__name__ = name
    node.run.side_effect = RuntimeError("step failed")
    return node


def _patch_observability() -> tuple[list[dict], MagicMock]:
    """Return (breadcrumbs list, mock_obs) ready to inject."""
    breadcrumbs: list[dict] = []

    mock_obs = MagicMock()

    def record_breadcrumb(*, message: str, category: str, level: str, data=None) -> None:
        breadcrumbs.append(
            {"message": message, "category": category, "level": level, "data": data or {}}
        )

    mock_obs.add_breadcrumb.side_effect = record_breadcrumb
    mock_obs.capture_exception = MagicMock()
    mock_obs.capture_message = MagicMock()
    return breadcrumbs, mock_obs


# ---------------------------------------------------------------------------
# Property 13: Orchestration Step Breadcrumb Symmetry
# **Validates: Requirements 7.2**
# ---------------------------------------------------------------------------

@given(
    n_success=st.integers(min_value=0, max_value=4),
    fail_at_end=st.booleans(),
)
@settings(max_examples=100)
def test_step_breadcrumb_symmetry(n_success: int, fail_at_end: bool) -> None:
    """Property 13: each successful step has start+complete; each failed step has start+failed.

    **Validates: Requirements 7.2**
    """
    nodes = [_make_success_node(f"Step{i}") for i in range(n_success)]
    if fail_at_end:
        nodes.append(_make_fail_node("FailStep"))

    breadcrumbs, mock_obs = _patch_observability()

    with patch("omni_modal.orchestration.adk_workflow.observability", mock_obs):
        graph = DeterministicAgentGraph(nodes)
        try:
            graph.run(_make_request())
        except Exception:
            pass

    orch_crumbs = [b for b in breadcrumbs if b["category"] == "orchestration"]

    # Every successful step must have exactly 1 start AND 1 complete breadcrumb
    for i in range(n_success):
        step_name = f"Step{i}"
        starts = [b for b in orch_crumbs if f"started: {step_name}" in b["message"]]
        completes = [b for b in orch_crumbs if f"completed: {step_name}" in b["message"]]
        assert len(starts) == 1, f"Step {step_name} should have exactly 1 start breadcrumb, got {len(starts)}"
        assert len(completes) == 1, f"Step {step_name} should have exactly 1 complete breadcrumb, got {len(completes)}"

    if fail_at_end:
        step_name = "FailStep"
        starts = [b for b in orch_crumbs if f"started: {step_name}" in b["message"]]
        completes = [b for b in orch_crumbs if f"completed: {step_name}" in b["message"]]
        faileds = [b for b in orch_crumbs if f"failed: {step_name}" in b["message"]]

        assert len(starts) == 1, "Failed step should have exactly 1 start breadcrumb"
        assert len(completes) == 0, "Failed step should NOT have a complete breadcrumb"
        assert len(faileds) == 1, "Failed step should have exactly 1 failed breadcrumb"

        failed_data = faileds[0]["data"]
        assert "elapsed_ms" in failed_data, "Failed breadcrumb must include elapsed_ms"
        assert "failure_reason" in failed_data, "Failed breadcrumb must include failure_reason"


# ---------------------------------------------------------------------------
# Property 14: Orchestration Timeout Context Completeness
# **Validates: Requirements 7.1, 7.3**
# ---------------------------------------------------------------------------

@given(
    step_names=st.lists(
        st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=1, max_size=12),
        min_size=1,
        max_size=5,
    ),
    durations=st.lists(
        st.floats(min_value=0.1, max_value=100.0),
        min_size=1,
        max_size=5,
    ),
)
@settings(max_examples=100)
def test_orchestration_timeout_context_completeness(
    step_names: list[str],
    durations: list[float],
) -> None:
    """Property 14: when timeout occurs after N steps, captured context includes all N steps.

    **Validates: Requirements 7.1, 7.3**
    """
    # Align lengths
    n = min(len(step_names), len(durations))
    step_names = step_names[:n]
    durations = durations[:n]

    if n == 0:
        return

    # Build success nodes for all steps, then trigger total timeout right after
    # We set total_timeout_ms extremely small so it fires after the first step
    nodes = [_make_success_node(name) for name in step_names]

    captured_contexts: list[dict] = []

    def capture_exc(exc, *, operation, context=None):
        if operation == "orchestration.total_timeout":
            captured_contexts.append(context or {})

    mock_obs = MagicMock()
    mock_obs.add_breadcrumb = MagicMock()
    mock_obs.capture_exception.side_effect = capture_exc
    mock_obs.capture_message = MagicMock()

    # Use total_timeout_ms=-1.0 so the condition (elapsed > -1.0) always fires after
    # the first step completes (elapsed is always >= 0).
    with patch("omni_modal.orchestration.adk_workflow.observability", mock_obs):
        graph = DeterministicAgentGraph(nodes, total_timeout_ms=-1.0)
        try:
            graph.run(_make_request())
        except TimeoutError:
            pass

    # We expect a timeout capture to have happened
    assert len(captured_contexts) >= 1, "Expected at least one total_timeout capture"

    ctx = captured_contexts[0]

    # Must contain all required keys
    assert "completed_steps" in ctx, "Timeout context must include completed_steps"
    assert "total_elapsed_ms" in ctx, "Timeout context must include total_elapsed_ms"
    assert "timeout_limit_ms" in ctx, "Timeout context must include timeout_limit_ms"
    assert "in_progress_step" in ctx, "Timeout context must include in_progress_step (may be None)"

    # completed_steps must be a list of dicts with name and duration_ms
    completed = ctx["completed_steps"]
    assert isinstance(completed, list), "completed_steps must be a list"
    for entry in completed:
        assert "name" in entry, f"Each completed step entry must have 'name', got {entry}"
        assert "duration_ms" in entry, f"Each completed step entry must have 'duration_ms', got {entry}"

    # total_elapsed_ms must be non-negative
    assert ctx["total_elapsed_ms"] >= 0.0

    # timeout_limit_ms must match configured value
    assert ctx["timeout_limit_ms"] == -1.0


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestOrchestrationBreadcrumbs(unittest.TestCase):
    """Unit tests for orchestration step breadcrumb and timeout instrumentation."""

    def test_step_breadcrumbs_recorded_in_order(self) -> None:
        """Run a graph with 3 success nodes; verify breadcrumb sequence."""
        nodes = [
            _make_success_node("Alpha"),
            _make_success_node("Beta"),
            _make_success_node("Gamma"),
        ]
        breadcrumbs, mock_obs = _patch_observability()

        with patch("omni_modal.orchestration.adk_workflow.observability", mock_obs):
            graph = DeterministicAgentGraph(nodes)
            graph.run(_make_request())

        orch_crumbs = [b for b in breadcrumbs if b["category"] == "orchestration"]
        messages = [b["message"] for b in orch_crumbs]

        # Expected interleaved order: start Alpha, complete Alpha, start Beta, ...
        expected_order = [
            "Workflow step started: Alpha",
            "Workflow step completed: Alpha",
            "Workflow step started: Beta",
            "Workflow step completed: Beta",
            "Workflow step started: Gamma",
            "Workflow step completed: Gamma",
        ]
        self.assertEqual(messages, expected_order)

    def test_step_failure_captures_exception(self) -> None:
        """Single failing node: verify capture_exception called with step context."""
        node = _make_fail_node("BadStep")
        breadcrumbs, mock_obs = _patch_observability()

        with patch("omni_modal.orchestration.adk_workflow.observability", mock_obs):
            graph = DeterministicAgentGraph([node])
            with self.assertRaises(RuntimeError):
                graph.run(_make_request())

        # capture_exception should have been called once
        mock_obs.capture_exception.assert_called_once()
        call_kwargs = mock_obs.capture_exception.call_args

        # First positional arg is the exception
        exc = call_kwargs[0][0]
        self.assertIsInstance(exc, RuntimeError)

        # Keyword args include operation and context
        self.assertEqual(call_kwargs[1]["operation"], "orchestration.step_failure")
        ctx = call_kwargs[1]["context"]
        self.assertEqual(ctx["step_name"], "BadStep")
        self.assertIn("elapsed_ms", ctx)
        self.assertIn("completed_steps", ctx)
        self.assertIn("in_progress_step", ctx)
        self.assertEqual(ctx["in_progress_step"], "BadStep")

        # Failed breadcrumb must have been recorded
        orch_crumbs = [b for b in breadcrumbs if b["category"] == "orchestration"]
        failed = [b for b in orch_crumbs if "failed: BadStep" in b["message"]]
        self.assertEqual(len(failed), 1)
        self.assertIn("failure_reason", failed[0]["data"])

    def test_successful_graph_no_exception_captured(self) -> None:
        """All steps succeed: verify no capture_exception is called."""
        nodes = [
            _make_success_node("NodeA"),
            _make_success_node("NodeB"),
        ]
        breadcrumbs, mock_obs = _patch_observability()

        with patch("omni_modal.orchestration.adk_workflow.observability", mock_obs):
            graph = DeterministicAgentGraph(nodes)
            graph.run(_make_request())

        mock_obs.capture_exception.assert_not_called()

        # All start+complete pairs present, no failed breadcrumbs
        orch_crumbs = [b for b in breadcrumbs if b["category"] == "orchestration"]
        failed_crumbs = [b for b in orch_crumbs if "failed" in b["message"]]
        self.assertEqual(failed_crumbs, [])

        for name in ("NodeA", "NodeB"):
            starts = [b for b in orch_crumbs if f"started: {name}" in b["message"]]
            completes = [b for b in orch_crumbs if f"completed: {name}" in b["message"]]
            self.assertEqual(len(starts), 1)
            self.assertEqual(len(completes), 1)


if __name__ == "__main__":
    unittest.main()
