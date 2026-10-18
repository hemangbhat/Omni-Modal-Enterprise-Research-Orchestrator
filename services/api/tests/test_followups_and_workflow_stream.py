"""Follow-up query rewriting and the streaming orchestration workflow."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from omni_modal.orchestration.adk_workflow import AgentWorkflowResult, InternalResearchAdkWorkflow
from omni_modal.qa.in_memory_store import InMemoryChunkRetriever, InMemoryVectorStore, StoredChunk
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.models import QueryRequest, RetrievedChunk
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer
from omni_modal.research.followups import QueryRewriter


class _FakeClient:
    name = "fake"

    def __init__(self, reply: str = "", fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.calls: list[list[dict]] = []

    def complete(self, system, messages, *, max_tokens=1024):
        self.calls.append(messages)
        if self.fail:
            raise OSError("down")
        return self.reply

    def stream(self, system, messages, *, max_tokens=1024):
        yield self.complete(system, messages)


HISTORY = [("user", "What was Q3 revenue growth?"), ("assistant", "## Answer\n\n18% [1]")]


class QueryRewriterTests(unittest.TestCase):
    def test_first_question_is_used_as_is(self) -> None:
        client = _FakeClient("unused")
        self.assertEqual(QueryRewriter(client).rewrite("What was Q3 revenue?", []), "What was Q3 revenue?")
        self.assertEqual(client.calls, [])

    def test_follow_up_is_rewritten_by_the_model(self) -> None:
        client = _FakeClient('"Q3 headcount growth"\n')
        out = QueryRewriter(client).rewrite("and headcount?", HISTORY)
        self.assertEqual(out, "Q3 headcount growth")
        self.assertIn("and headcount?", client.calls[0][-1]["content"])
        self.assertIn("What was Q3 revenue growth?", client.calls[0][-1]["content"])

    def test_without_a_model_the_previous_question_is_prepended(self) -> None:
        self.assertEqual(
            QueryRewriter(None).rewrite("and headcount?", HISTORY),
            "What was Q3 revenue growth? and headcount?",
        )

    def test_model_failure_or_junk_falls_back_to_the_heuristic(self) -> None:
        for client in (_FakeClient(fail=True), _FakeClient(""), _FakeClient("x" * 1000)):
            self.assertEqual(
                QueryRewriter(client).rewrite("and headcount?", HISTORY),
                "What was Q3 revenue growth? and headcount?",
            )


class _RecordingRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.seen: list[QueryRequest] = []

    def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
        self.seen.append(request)
        return self.chunks


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(chunk_id="c1", document_id="d1", title="Q3", source_type="pdf",
                          chunk_index=0, content="Headcount rose to 145.", similarity=0.9)


class WorkflowStreamTests(unittest.TestCase):
    def _run(self, workflow, request):
        items = list(workflow.answer_stream(request))
        final = items[-1]
        self.assertIsInstance(final, AgentWorkflowResult)
        return [i for i in items[:-1]], final

    def test_retrieval_uses_the_rewritten_query_and_synthesis_the_original(self) -> None:
        retriever = _RecordingRetriever([_chunk()])
        wf = InternalResearchAdkWorkflow(retriever, synthesizer=ExtractiveAnswerSynthesizer())
        req = QueryRequest(tenant_id="t1", user_id="u1", question="and headcount?",
                           search_query="Q3 headcount", history=tuple(HISTORY))
        deltas, final = self._run(wf, req)
        self.assertEqual(retriever.seen[0].question, "Q3 headcount")
        self.assertEqual(final.response.question, "and headcount?")
        self.assertEqual("".join(deltas), final.response.answer_markdown)

    def test_trace_matches_the_non_streaming_path(self) -> None:
        retriever = _RecordingRetriever([_chunk()])
        wf = InternalResearchAdkWorkflow(retriever, synthesizer=ExtractiveAnswerSynthesizer())
        req = QueryRequest(tenant_id="t1", user_id="u1", question="headcount?")
        _, streamed = self._run(wf, req)
        whole = wf.answer(req)
        self.assertEqual(streamed.trace, whole.trace)
        self.assertEqual(streamed.response.answer_markdown, whole.response.answer_markdown)

    def test_no_evidence_path_streams_the_fallback_text(self) -> None:
        wf = InternalResearchAdkWorkflow(_RecordingRetriever([]), synthesizer=ExtractiveAnswerSynthesizer())
        deltas, final = self._run(wf, QueryRequest(tenant_id="t1", user_id="u1", question="anything"))
        self.assertEqual(final.response.status, "no_data")
        self.assertEqual("".join(deltas), final.response.answer_markdown)

    def test_a_slow_reader_does_not_count_against_the_time_budget(self) -> None:
        import time

        from omni_modal.orchestration.adk_workflow import DeterministicAgentGraph
        from omni_modal.orchestration.graph_nodes import (
            ControlledFallbackNode, InternalRetrievalNode, MissingDataDetectionNode,
            ReasoningSynthesisNode, ValidateRequestNode,
        )

        graph = DeterministicAgentGraph(
            [ValidateRequestNode(), InternalRetrievalNode(_RecordingRetriever([_chunk()])),
             MissingDataDetectionNode(), ReasoningSynthesisNode(ExtractiveAnswerSynthesizer()),
             ControlledFallbackNode()],
            total_timeout_ms=150,
        )
        items = []
        for item in graph.run_stream(QueryRequest(tenant_id="t1", user_id="u1", question="headcount")):
            items.append(item)
            time.sleep(0.08)  # the client reads slowly
        self.assertIsInstance(items[-1], AgentWorkflowResult)

    def test_streams_over_the_real_in_memory_retriever(self) -> None:
        provider = HashingQueryEmbeddingProvider()
        store = InMemoryVectorStore()
        store.add(StoredChunk(tenant_id="t1", document_id="d1", title="Q3", source_type="pdf",
                              chunk_id="c1", chunk_index=0, content="Headcount rose to 145.",
                              embedding=provider.embed_query("Headcount rose to 145.")))
        wf = InternalResearchAdkWorkflow(InMemoryChunkRetriever(provider, store),
                                         synthesizer=ExtractiveAnswerSynthesizer())
        _, final = self._run(wf, QueryRequest(tenant_id="t1", user_id="u1", question="headcount"))
        self.assertEqual(final.response.status, "answered")
        self.assertEqual(final.response.citations[0].snippet, "Headcount rose to 145.")


if __name__ == "__main__":
    unittest.main()
