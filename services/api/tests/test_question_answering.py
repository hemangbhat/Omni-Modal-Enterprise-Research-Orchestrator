import unittest

import _path  # noqa: F401
from omni_modal.qa import (
    ExtractiveAnswerSynthesizer,
    HashingQueryEmbeddingProvider,
    InternalQuestionAnsweringService,
    QueryContractError,
    QueryRequest,
    RetrievedChunk,
    query_request_from_payload,
    stream_markdown,
)


class FakeRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.last_request: QueryRequest | None = None
        self._chunks = chunks

    def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
        self.last_request = request
        return self._chunks[: request.top_k]


class QuestionAnsweringTest(unittest.TestCase):
    def test_query_contract_accepts_valid_payload(self) -> None:
        request = query_request_from_payload(
            {
                "tenant_id": "tenant",
                "user_id": "user",
                "question": "What risks were raised?",
                "top_k": 3,
                "min_similarity": 0.2,
            }
        )

        self.assertEqual(request.top_k, 3)
        self.assertEqual(request.min_similarity, 0.2)

    def test_query_contract_rejects_empty_question(self) -> None:
        with self.assertRaises(QueryContractError):
            query_request_from_payload(
                {"tenant_id": "tenant", "user_id": "user", "question": " "}
            )

    def test_service_answers_with_citations_from_retrieved_chunks(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="chunk-1",
            document_id="doc-1",
            title="Buyer interviews",
            source_type="pdf",
            chunk_index=0,
            content="Security review delays are a risk for enterprise renewals.",
            similarity=0.91,
            metadata={"page_number": 4},
        )
        service = InternalQuestionAnsweringService(FakeRetriever([chunk]))

        response = service.answer(
            QueryRequest(
                tenant_id="tenant",
                user_id="user",
                question="What risks were raised?",
            )
        )

        self.assertEqual(response.status, "answered")
        self.assertIn("Security review delays", response.answer_markdown)
        self.assertIn("[1]", response.answer_markdown)
        self.assertEqual(response.citations[0].page_number, 4)

    def test_service_says_no_data_instead_of_guessing(self) -> None:
        service = InternalQuestionAnsweringService(FakeRetriever([]))

        response = service.answer(
            QueryRequest(
                tenant_id="tenant",
                user_id="user",
                question="What is the renewal forecast?",
            )
        )

        self.assertEqual(response.status, "no_data")
        self.assertIn("could not find relevant internal document data", response.answer_markdown)
        self.assertEqual(response.citations, [])

    def test_stream_markdown_splits_response_deterministically(self) -> None:
        chunks = stream_markdown("abcdef", chunk_size=2)

        self.assertEqual(chunks, ["ab", "cd", "ef"])

    def test_hashing_embedding_is_deterministic(self) -> None:
        provider = HashingQueryEmbeddingProvider(dimensions=16)

        self.assertEqual(provider.embed_query("risk deadline"), provider.embed_query("risk deadline"))


if __name__ == "__main__":
    unittest.main()
