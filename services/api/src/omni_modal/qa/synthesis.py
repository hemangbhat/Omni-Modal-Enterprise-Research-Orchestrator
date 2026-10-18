from __future__ import annotations

from collections.abc import Iterator

from omni_modal.qa.citations import best_excerpt
from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk


class ExtractiveAnswerSynthesizer:
    """No-LLM answers: the most relevant sentence of each retrieved passage."""

    def synthesize(
        self, request: QueryRequest, chunks: list[RetrievedChunk]
    ) -> QueryResponse:
        if not chunks:
            return QueryResponse(
                status="no_data",
                question=request.question,
                answer_markdown=(
                    "## Answer\n\n"
                    "I could not find relevant internal document data for this question. "
                    "No answer was generated."
                ),
                citations=[],
                retrieved_chunks=[],
            )

        query = request.retrieval_text
        bullets = []
        citations = []
        for index, chunk in enumerate(chunks, start=1):
            excerpt = best_excerpt(chunk.content, query, limit=420)
            bullets.append(f"- {excerpt} [{index}]")
            citations.append(chunk.source_reference(snippet=best_excerpt(chunk.content, query), cited=True))

        sources = [
            f"{index}. {citation.citation_label(index)}"
            for index, citation in enumerate(citations, start=1)
        ]
        answer = (
            "## Answer\n\n"
            "Based only on the retrieved internal documents:\n\n"
            + "\n".join(bullets)
            + "\n\n## Sources\n\n"
            + "\n".join(sources)
        )
        return QueryResponse(
            status="answered",
            question=request.question,
            answer_markdown=answer,
            citations=citations,
            retrieved_chunks=chunks,
        )

    def synthesize_stream(
        self, request: QueryRequest, chunks: list[RetrievedChunk]
    ) -> Iterator[str | QueryResponse]:
        """There is no model to stream from, so stream the finished text."""
        response = self.synthesize(request, chunks)
        yield from stream_markdown(response.answer_markdown)
        yield response


def stream_markdown(markdown: str, chunk_size: int = 80) -> list[str]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be greater than zero.")
    return [markdown[index : index + chunk_size] for index in range(0, len(markdown), chunk_size)]
