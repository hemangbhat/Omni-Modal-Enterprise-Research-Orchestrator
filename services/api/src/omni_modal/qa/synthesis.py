from __future__ import annotations

import re

from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk, SourceReference


class ExtractiveAnswerSynthesizer:
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

        citations = [chunk.source_reference() for chunk in chunks]
        bullets = []
        for index, chunk in enumerate(chunks, start=1):
            excerpt = _best_excerpt(chunk.content, request.question)
            bullets.append(f"- {excerpt} [{index}]")

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


def stream_markdown(markdown: str, chunk_size: int = 80) -> list[str]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be greater than zero.")
    return [markdown[index : index + chunk_size] for index in range(0, len(markdown), chunk_size)]


def _best_excerpt(content: str, question: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", content.strip())
    if not sentences:
        return content.strip()

    terms = {term.lower() for term in re.findall(r"[A-Za-z0-9_]+", question)}
    ranked = sorted(
        sentences,
        key=lambda sentence: sum(
            1 for term in re.findall(r"[A-Za-z0-9_]+", sentence.lower()) if term in terms
        ),
        reverse=True,
    )
    excerpt = ranked[0].strip() if ranked else content.strip()
    if len(excerpt) <= 420:
        return excerpt
    return excerpt[:417].rstrip() + "..."
