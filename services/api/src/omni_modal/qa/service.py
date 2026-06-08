from __future__ import annotations

from omni_modal.qa.models import QueryRequest, QueryResponse
from omni_modal.qa.retrieval import ChunkRetriever
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer
from omni_modal.observability import observability


class InternalQuestionAnsweringService:
    def __init__(
        self,
        retriever: ChunkRetriever,
        synthesizer: ExtractiveAnswerSynthesizer | None = None,
    ) -> None:
        self._retriever = retriever
        self._synthesizer = synthesizer or ExtractiveAnswerSynthesizer()

    def answer(self, request: QueryRequest) -> QueryResponse:
        try:
            chunks = self._retriever.retrieve(request)
            return self._synthesizer.synthesize(request, chunks)
        except Exception as exc:
            observability.capture_exception(
                exc,
                operation="qa.answer",
                context={"tenant_id": request.tenant_id, "top_k": request.top_k},
            )
            return QueryResponse(
                status="failed",
                question=request.question,
                answer_markdown=(
                    "## Answer\n\n"
                    "The internal question-answering service failed before it could "
                    "produce an evidence-backed answer."
                ),
                citations=[],
                retrieved_chunks=[],
                error_message=str(exc),
            )
