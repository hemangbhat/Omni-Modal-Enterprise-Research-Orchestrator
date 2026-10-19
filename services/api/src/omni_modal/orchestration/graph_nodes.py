from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from typing import Protocol

from omni_modal.orchestration.a2a import (
    ExternalResearchClient,
    build_a2a_request,
)
from omni_modal.orchestration.graph_models import AgentGraphState
from omni_modal.qa.citations import NO_EVIDENCE_SENTENCE, best_excerpt
from omni_modal.qa.models import QueryResponse
from omni_modal.qa.synthesis import stream_markdown
from omni_modal.research import external
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
            # A follow-up is retrieved with its standalone rewrite.
            request = state.request
            if request.search_query:
                request = replace(request, question=request.search_query)
            state.retrieved_chunks = self._retriever.retrieve(request)
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

    def _skip(self, state: AgentGraphState) -> bool:
        if state.error_message or state.missing_data:
            state.add_trace(
                "synthesize_answer",
                "skipped",
                "Skipped because internal evidence is unavailable or insufficient.",
            )
            return True
        return False

    def _done(self, state: AgentGraphState) -> AgentGraphState:
        state.add_trace(
            "synthesize_answer",
            "ok",
            "Built Markdown answer from retrieved internal evidence only.",
        )
        return state

    def run(self, state: AgentGraphState) -> AgentGraphState:
        if self._skip(state):
            return state
        state.response = self._synthesizer.synthesize(
            state.request, state.retrieved_chunks
        )
        return self._done(state)

    def stream(self, state: AgentGraphState) -> Iterator[str]:
        """Like ``run``, but yields answer text as the synthesizer produces it."""
        if self._skip(state):
            return
        stream = getattr(self._synthesizer, "synthesize_stream", None)
        if stream is None:
            state.response = self._synthesizer.synthesize(state.request, state.retrieved_chunks)
            yield state.response.answer_markdown
        else:
            for item in stream(state.request, state.retrieved_chunks):
                if isinstance(item, QueryResponse):
                    state.response = item
                else:
                    yield item
        self._done(state)


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


class WebResearchNode:
    """When the documents cannot answer, search the web, if the workspace allows it.

    Runs after internal synthesis. The question is redacted before it leaves
    (see ``research.external``); results are labelled external and appended
    below the internal "not enough evidence" answer, never merged into it.
    """

    def __init__(self, client, synthesizer=None) -> None:
        self._client = client
        self._synthesizer = synthesizer or ExtractiveAnswerSynthesizer()

    def run(self, state: AgentGraphState) -> AgentGraphState:
        self._execute(state)
        return state

    def stream(self, state: AgentGraphState) -> Iterator[str]:
        text = self._execute(state)
        if text:
            yield from stream_markdown(text)

    def _applies(self, state: AgentGraphState) -> bool:
        if state.error_message or state.request.external_permit is None:
            return False
        if state.response is None:
            return state.missing_data
        return state.response.status == "no_data"

    def _execute(self, state: AgentGraphState) -> str | None:
        """Updates the state; returns text to append to what was already streamed."""
        if not self._applies(state):
            state.add_trace("web_research", "skipped", "Not needed or not allowed for this question.")
            return None
        if self._client is None:
            return self._note(state, "no web search provider is configured.")
        internal = [c.content for c in state.retrieved_chunks]
        query, usable = external.redact_question(state.request.retrieval_text, internal)
        if not usable:
            return self._note(state, "too little of the question was left after removing private details.")
        reason = state.request.external_permit()
        if reason:
            return self._note(state, reason)
        try:
            results = self._client.search(query)
        except Exception as exc:  # noqa: BLE001 - a failed search must not fail the answer
            external.log_failure(exc, state.request.tenant_id)
            return self._note(state, "the web search did not respond.")
        if not results:
            return self._note(state, "the web search found nothing.")

        body, citations = self._answer(state, query, results)
        if body is None:
            return self._note(state, "the web results did not answer it either.")
        sources = "\n".join(f"{i}. [{safe_label(c.title)}]({c.url})" for i, c in enumerate(citations, start=1))
        section = (f"{external.WEB_HEADING}\n\n{body}\n\nSearched the web for: \u201c{query}\u201d\n\n"
                   f"### Web sources\n\n{sources}")
        prefix = "" if state.response is not None else "## Answer\n\n" + NO_EVIDENCE_SENTENCE
        appended = ("\n\n" if state.response is not None else "\n\n") + section
        before = state.response.answer_markdown if state.response is not None else prefix
        state.response = QueryResponse(
            status="answered", question=state.request.question, answer_markdown=before + appended,
            citations=citations, retrieved_chunks=state.retrieved_chunks,
        )
        state.add_trace("web_research", "ok", f"Answered from {len(results)} web results.")
        return appended if prefix == "" else prefix + appended

    def _answer(self, state: AgentGraphState, query: str, results):
        chunks = external.as_chunks(results)
        if isinstance(self._synthesizer, ExtractiveAnswerSynthesizer):
            lines, refs = [], []
            for i, chunk in enumerate(chunks[:3], start=1):
                excerpt = best_excerpt(chunk.content, query, limit=420)
                if excerpt:
                    lines.append(f"- {excerpt} [{len(refs) + 1}]")
                    refs.append(chunk.source_reference(snippet=best_excerpt(chunk.content, query), cited=True))
            body = "\n".join(lines) if lines else None
            return body, external.external_citations(refs, results)
        response = self._synthesizer.synthesize(external.web_request(state.request, query), chunks)
        if response.status != "answered":
            return None, []
        body = response.answer_markdown.split("\n## Sources", 1)[0]
        body = re.sub(r"^\s*## Answer\s*", "", body).strip()
        return body, external.external_citations(list(response.citations), results)

    def _note(self, state: AgentGraphState, reason: str) -> str | None:
        state.web_note = f"Web search was not used: {reason}"
        state.add_trace("web_research", "skipped", state.web_note)
        if state.response is None:
            return None  # the fallback answer carries the note
        state.response = replace(state.response, answer_markdown=state.response.answer_markdown + "\n\n" + state.web_note)
        return "\n\n" + state.web_note


def safe_label(text: str) -> str:
    return " ".join(str(text).replace("[", "(").replace("]", ")").split())


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
            if state.web_note:
                delegation_detail = state.web_note
            elif state.external_response is not None and state.external_response.status == "disabled":
                delegation_detail = "External research is not turned on, so nothing was searched outside your documents."
            elif state.external_delegation_attempted:
                delegation_detail = "External delegation did not return usable findings."
            else:
                delegation_detail = "No external delegation was attempted."
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
