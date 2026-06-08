import unittest

import _path  # noqa: F401
from omni_modal.orchestration import (
    A2AResearchRequest,
    A2AResearchResponse,
    ExternalResearchFinding,
    InternalResearchAdkWorkflow,
)
from omni_modal.qa import QueryRequest, RetrievedChunk


class TraceRetriever:
    def __init__(self, chunks: list[RetrievedChunk], calls: list[str]) -> None:
        self._chunks = chunks
        self._calls = calls

    def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
        self._calls.append("retrieve")
        return self._chunks


class FakeExternalClient:
    def __init__(self, response: A2AResearchResponse) -> None:
        self.last_request: A2AResearchRequest | None = None
        self._response = response

    def delegate(self, request: A2AResearchRequest) -> A2AResearchResponse:
        self.last_request = request
        return self._response


class AdkWorkflowTest(unittest.TestCase):
    def test_workflow_retrieves_before_answering(self) -> None:
        calls: list[str] = []
        workflow = InternalResearchAdkWorkflow(
            TraceRetriever(
                [
                    RetrievedChunk(
                        chunk_id="chunk-1",
                        document_id="doc-1",
                        title="Renewal packet",
                        source_type="pdf",
                        chunk_index=0,
                        content="Security review risk may delay approval.",
                        similarity=0.9,
                        metadata={"page_number": 2},
                    )
                ],
                calls,
            )
        )

        result = workflow.answer(
            QueryRequest(
                tenant_id="tenant",
                user_id="user",
                question="What risk may delay approval?",
            )
        )

        self.assertEqual(calls, ["retrieve"])
        self.assertEqual(result.response.status, "answered")
        self.assertIn("Security review risk", result.response.answer_markdown)
        self.assertEqual(
            [item["node"] for item in result.trace],
            [
                "validate_request",
                "retrieve_internal_evidence",
                "detect_missing_data",
                "delegate_external_research",
                "merge_external_evidence",
                "synthesize_answer",
                "controlled_fallback",
            ],
        )
        self.assertEqual(result.trace[3]["status"], "skipped")
        self.assertEqual(result.trace[4]["status"], "skipped")
        self.assertEqual(result.trace[5]["status"], "ok")
        self.assertEqual(result.trace[6]["status"], "skipped")

    def test_workflow_detects_missing_internal_data(self) -> None:
        workflow = InternalResearchAdkWorkflow(TraceRetriever([], []))

        result = workflow.answer(
            QueryRequest(
                tenant_id="tenant",
                user_id="user",
                question="What is the forecast?",
            )
        )

        self.assertEqual(result.response.status, "no_data")
        self.assertIn("External delegation did not return usable findings", result.response.answer_markdown)
        self.assertEqual(result.trace[2]["node"], "detect_missing_data")
        self.assertIn("insufficient", result.trace[2]["detail"])
        self.assertEqual(result.trace[3]["node"], "delegate_external_research")
        self.assertEqual(result.trace[3]["status"], "failed")
        self.assertEqual(result.trace[4]["status"], "skipped")
        self.assertEqual(result.trace[-1]["node"], "controlled_fallback")

    def test_workflow_merges_external_findings_when_internal_data_is_missing(self) -> None:
        external_client = FakeExternalClient(
            A2AResearchResponse(
                request_id="external-1",
                status="ok",
                findings=[
                    ExternalResearchFinding(
                        claim="Public filings mention longer enterprise procurement cycles.",
                        source_title="Example public filing",
                        source_url="https://example.com/filing",
                        confidence=0.7,
                    )
                ],
            )
        )
        workflow = InternalResearchAdkWorkflow(
            TraceRetriever([], []),
            external_client,
        )

        result = workflow.answer(
            QueryRequest(
                tenant_id="tenant",
                user_id="user",
                question="What external signals explain deal delays?",
            )
        )

        self.assertIsNotNone(external_client.last_request)
        self.assertFalse(external_client.last_request.to_message()["metadata"]["contains_internal_content"])
        self.assertEqual(result.response.status, "answered")
        self.assertIn("## External findings", result.response.answer_markdown)
        self.assertIn("[E1]", result.response.answer_markdown)
        self.assertEqual(result.trace[3]["status"], "ok")
        self.assertEqual(result.trace[4]["status"], "ok")
        self.assertEqual(result.trace[5]["status"], "skipped")

    def test_workflow_uses_controlled_failure_for_retrieval_errors(self) -> None:
        class FailingRetriever:
            def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
                raise RuntimeError("database unavailable")

        workflow = InternalResearchAdkWorkflow(FailingRetriever())

        result = workflow.answer(
            QueryRequest(
                tenant_id="tenant",
                user_id="user",
                question="What risk was raised?",
            )
        )

        self.assertEqual(result.response.status, "failed")
        self.assertEqual(result.response.error_message, "database unavailable")
        self.assertEqual(result.trace[1]["status"], "failed")
        self.assertEqual(result.trace[-1]["node"], "controlled_fallback")


if __name__ == "__main__":
    unittest.main()
