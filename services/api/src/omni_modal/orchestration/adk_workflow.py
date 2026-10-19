from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

from omni_modal.observability import observability
from omni_modal.orchestration.graph_models import AgentGraphState
from omni_modal.orchestration.graph_nodes import (
    ControlledFallbackNode,
    ExternalDelegationNode,
    ExternalEvidenceMergeNode,
    GraphNode,
    InternalRetrievalNode,
    MissingDataDetectionNode,
    ReasoningSynthesisNode,
    ValidateRequestNode,
    WebResearchNode,
)
from omni_modal.orchestration.a2a import (
    DisabledExternalResearchClient,
    ExternalResearchClient,
)
from omni_modal.qa.models import QueryRequest, QueryResponse
from omni_modal.qa.retrieval import ChunkRetriever
from omni_modal.qa.synthesis import stream_markdown


@dataclass(frozen=True)
class AgentWorkflowResult:
    response: QueryResponse
    trace: list[dict[str, str]]


class DeterministicAgentGraph:
    def __init__(
        self,
        nodes: list[GraphNode],
        step_timeout_ms: float = 30_000.0,
        total_timeout_ms: float = 120_000.0,
    ) -> None:
        self._nodes = nodes
        self._step_timeout_ms = step_timeout_ms
        self._total_timeout_ms = total_timeout_ms

    def run(self, request: QueryRequest) -> AgentWorkflowResult:
        result = None
        for item in self._execute(request, streaming=False):
            result = item
        assert isinstance(result, AgentWorkflowResult)
        return result

    def run_stream(self, request: QueryRequest) -> Iterator[str | AgentWorkflowResult]:
        """Run the same nodes, yielding answer text as it is produced, then the result.

        A node with a ``stream`` method (the synthesis step) streams its text.
        When no node streamed (no evidence, or a failure), the final answer's
        text is streamed once the graph finishes.
        """
        streamed = False
        for item in self._execute(request, streaming=True):
            if isinstance(item, AgentWorkflowResult):
                if not streamed:
                    yield from stream_markdown(item.response.answer_markdown)
                yield item
            else:
                streamed = True
                yield item

    def _execute(self, request: QueryRequest, *, streaming: bool) -> Iterator[str | AgentWorkflowResult]:
        state = AgentGraphState(request=request)
        completed_steps: list[dict[str, float]] = []
        workflow_start = time.monotonic()

        for node in self._nodes:
            step_name = type(node).__name__
            step_start = time.monotonic()

            # Start breadcrumb
            observability.add_breadcrumb(
                message=f"Workflow step started: {step_name}",
                category="orchestration",
                level="info",
                data={"step_name": step_name, "timestamp": step_start},
            )

            try:
                node_stream = getattr(node, "stream", None) if streaming else None
                if node_stream is not None:
                    for item in node_stream(state):
                        handed_off = time.monotonic()
                        yield item
                        # Time the client spends reading is not the workflow's:
                        # shift both clocks past it.
                        waited = time.monotonic() - handed_off
                        step_start += waited
                        workflow_start += waited
                else:
                    state = node.run(state)
                elapsed_ms = (time.monotonic() - step_start) * 1000
                completed_steps.append({"name": step_name, "duration_ms": elapsed_ms})

                # Complete breadcrumb
                observability.add_breadcrumb(
                    message=f"Workflow step completed: {step_name}",
                    category="orchestration",
                    level="info",
                    data={"step_name": step_name, "elapsed_ms": elapsed_ms},
                )

                # Check step timeout (non-fatal — warn but continue)
                if elapsed_ms > self._step_timeout_ms:
                    observability.capture_message(
                        f"Step {step_name} exceeded timeout",
                        operation="orchestration.step_timeout",
                        context={
                            "step_name": step_name,
                            "elapsed_ms": elapsed_ms,
                            "timeout_limit_ms": self._step_timeout_ms,
                        },
                        level="warning",
                    )

            except Exception as exc:
                elapsed_ms = (time.monotonic() - step_start) * 1000

                # Failed breadcrumb (no complete breadcrumb)
                observability.add_breadcrumb(
                    message=f"Workflow step failed: {step_name}",
                    category="orchestration",
                    level="error",
                    data={
                        "step_name": step_name,
                        "elapsed_ms": elapsed_ms,
                        "failure_reason": str(exc),
                    },
                )

                # Capture exception with context
                total_elapsed_ms = (time.monotonic() - workflow_start) * 1000
                observability.capture_exception(
                    exc,
                    operation="orchestration.step_failure",
                    context={
                        "step_name": step_name,
                        "elapsed_ms": elapsed_ms,
                        "timeout_limit_ms": self._step_timeout_ms,
                        "completed_steps": completed_steps,
                        "in_progress_step": step_name,
                        "total_elapsed_ms": total_elapsed_ms,
                    },
                )
                raise

            # Check overall workflow budget
            total_elapsed_ms = (time.monotonic() - workflow_start) * 1000
            if total_elapsed_ms > self._total_timeout_ms:
                exc = TimeoutError(
                    f"Overall workflow timeout exceeded: {total_elapsed_ms:.0f}ms > {self._total_timeout_ms:.0f}ms"
                )
                observability.capture_exception(
                    exc,
                    operation="orchestration.total_timeout",
                    context={
                        "total_elapsed_ms": total_elapsed_ms,
                        "timeout_limit_ms": self._total_timeout_ms,
                        "completed_steps": completed_steps,
                        "in_progress_step": None,
                    },
                )
                raise exc

        if state.response is None:
            raise RuntimeError("Agent graph completed without a response.")
        yield AgentWorkflowResult(
            response=state.response,
            trace=state.to_trace_json(),
        )


class InternalResearchAdkWorkflow:
    """ADK-ready local workflow with fixed internal-first node ordering."""

    name = "internal_research_adk_workflow"

    def __init__(
        self,
        retriever: ChunkRetriever,
        external_client: ExternalResearchClient | None = None,
        synthesizer=None,
        web_search=None,
    ) -> None:
        client = external_client or DisabledExternalResearchClient()
        self._graph = DeterministicAgentGraph(
            [
                ValidateRequestNode(),
                InternalRetrievalNode(retriever),
                MissingDataDetectionNode(),
                ExternalDelegationNode(client),
                ExternalEvidenceMergeNode(),
                ReasoningSynthesisNode(synthesizer),
                WebResearchNode(web_search, synthesizer),
                ControlledFallbackNode(),
            ]
        )

    def answer(self, request: QueryRequest) -> AgentWorkflowResult:
        return self._graph.run(request)

    def answer_stream(self, request: QueryRequest) -> Iterator[str | AgentWorkflowResult]:
        return self._graph.run_stream(request)
