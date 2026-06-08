from __future__ import annotations

from typing import Protocol

from omni_modal.orchestration.a2a import (
    ExternalResearchClient,
    build_a2a_request,
)
from omni_modal.orchestration.graph_models import AgentGraphState
from omni_modal.qa.models import QueryResponse
from omni_modal.qa.retrieval import ChunkRetriever
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer


class GraphNode(Protocol):
    def run(self, state: AgentGraphState) -> AgentGraphState:
        raise NotImplementedError


class ValidateRequestNode:
    def run(self, state: AgentGraphState) -> AgentGraphState:
        if not state.request.question.strip():
            state.error_message = "Question is required."
            state.add_trace("validate_request", "failed", state.error_message)
            return state
        if state.request.top_k < 1:
            state.error_message = "top_k must be greater than zero."
            state.add_trace("validate_request", "failed", state.error_message)
            return state

        state.add_trace(
            "validate_request",
            "ok",
            "Request shape is valid and scoped to one tenant/user.",
        )
        return state


class InternalRetrievalNode:
    def __init__(self, retriever: ChunkRetriever) -> None:
        self._retriever = retriever

    def run(self, state: AgentGraphState) -> AgentGraphState:
        if state.error_message:
            state.add_trace(
                "retrieve_internal_evidence",
                "skipped",
                "Skipped because validation failed.",
            )
            return state

        try:
            state.retrieved_chunks = self._retriever.retrieve(state.request)
            state.add_trace(
                "retrieve_internal_evidence",
                "ok",
                f"Retrieved {len(state.retrieved_chunks)} internal chunks.",
            )
        except Exception as exc:
            state.error_message = str(exc)
            state.add_trace(
                "retrieve_internal_evidence",
                "failed",
                "Internal retrieval failed.",
            )
        return state


class MissingDataDetectionNode:
    def __init__(self, minimum_chunks: int = 1) -> None:
        self._minimum_chunks = minimum_chunks

    def run(self, state: AgentGraphState) -> AgentGraphState:
        if state.error_message:
            state.add_trace(
                "detect_missing_data",
                "skipped",
                "Skipped because an earlier node failed.",
            )
            return state

        state.missing_data = len(state.retrieved_chunks) < self._minimum_chunks
        state.add_trace(
            "detect_missing_data",
            "ok",
            "Internal evidence is insufficient."
            if state.missing_data
            else "Internal evidence is sufficient for extractive synthesis.",
        )
        return state


class ReasoningSynthesisNode:
    def __init__(self, synthesizer: ExtractiveAnswerSynthesizer | None = None) -> None:
        self._synthesizer = synthesizer or ExtractiveAnswerSynthesizer()

    def run(self, state: AgentGraphState) -> AgentGraphState:
        if state.error_message or state.missing_data:
            state.add_trace(
                "synthesize_answer",
                "skipped",
                "Skipped because internal evidence is unavailable or insufficient.",
            )
            return state

        state.response = self._synthesizer.synthesize(
            state.request, state.retrieved_chunks
        )
        state.add_trace(
            "synthesize_answer",
            "ok",
            "Built Markdown answer from retrieved internal evidence only.",
        )
        return state


class ExternalDelegationNode:
    def __init__(self, client: ExternalResearchClient) -> None:
        self._client = client

    def run(self, state: AgentGraphState) -> AgentGraphState:
        if state.error_message:
            state.add_trace(
                "delegate_external_research",
                "skipped",
                "Skipped because an earlier node failed.",
            )
            return state
        if not state.missing_data:
            state.add_trace(
                "delegate_external_research",
                "skipped",
                "Skipped because internal evidence was sufficient.",
            )
            return state

        request = build_a2a_request(
            tenant_id=state.request.tenant_id,
            user_id=state.request.user_id,
            question=state.request.question,
            reason="Internal retrieval did not return enough evidence to answer.",
            internal_status="insufficient",
        )
        state.external_delegation_attempted = True
        state.external_response = self._client.delegate(request)
        state.add_trace(
            "delegate_external_research",
            "ok" if state.external_response.status == "ok" else "failed",
            f"External delegation returned status {state.external_response.status}.",
        )
        return state


class ExternalEvidenceMergeNode:
    def run(self, state: AgentGraphState) -> AgentGraphState:
        if state.response is not None:
            state.add_trace(
                "merge_external_evidence",
                "skipped",
                "Skipped because internal synthesis already produced a response.",
            )
            return state
        if not state.external_delegation_attempted:
            state.add_trace(
                "merge_external_evidence",
                "skipped",
                "Skipped because external delegation was not attempted.",
            )
            return state
        if state.external_response is None or not state.external_response.findings:
            state.add_trace(
                "merge_external_evidence",
                "skipped",
                "Skipped because no external findings were returned.",
            )
            return state

        lines = [
            "## Answer",
            "",
            "Internal evidence was insufficient, so the unresolved portion was delegated externally.",
            "",
            "## Internal evidence",
            "",
            "No sufficient internal evidence was found for this question.",
            "",
            "## External findings",
            "",
        ]
        for index, finding in enumerate(state.external_response.findings, start=1):
            source = finding.source_title
            if finding.source_url:
                source = f"{source} ({finding.source_url})"
            lines.append(f"- {finding.claim} [E{index}]")
            lines.append(f"  - Source: {source}")

        state.response = QueryResponse(
            status="answered",
            question=state.request.question,
            answer_markdown="\n".join(lines),
            citations=[],
            retrieved_chunks=state.retrieved_chunks,
            error_message=None,
        )
        state.add_trace(
            "merge_external_evidence",
            "ok",
            f"Merged {len(state.external_response.findings)} external findings with explicit provenance.",
        )
        return state


class ControlledFallbackNode:
    def run(self, state: AgentGraphState) -> AgentGraphState:
        if state.response is not None:
            state.add_trace(
                "controlled_fallback",
                "skipped",
                "Skipped because synthesis produced an answer.",
            )
            return state

        if state.error_message:
            answer = (
                "## Answer\n\n"
                "I could not answer because the internal orchestration workflow failed "
                "before evidence-backed synthesis completed."
            )
            status = "failed"
            detail = "Returned controlled failure response."
        else:
            delegation_detail = (
                "External delegation did not return usable findings."
                if state.external_delegation_attempted
                else "No external delegation was attempted."
            )
            answer = (
                "## Answer\n\n"
                "I could not find enough internal evidence to answer this question. "
                f"{delegation_detail}"
            )
            status = "no_data"
            detail = (
                "Returned missing-data response after external delegation."
                if state.external_delegation_attempted
                else "Returned missing-data response without external delegation."
            )

        state.response = QueryResponse(
            status=status,  # type: ignore[arg-type]
            question=state.request.question,
            answer_markdown=answer,
            citations=[],
            retrieved_chunks=state.retrieved_chunks,
            error_message=state.error_message,
        )
        state.add_trace("controlled_fallback", "ok", detail)
        return state
