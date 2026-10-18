"""Turn a model's raw answer into a checked, cited ``QueryResponse``.

The model is asked to cite passages as ``[n]``. It can still invent numbers,
so markers outside the retrieved set are removed, every passage is flagged
``cited`` or not (keeping retrieval numbering, so ``[n]`` always matches card
``n``), and the model's "not enough information" reply becomes an explicit
``no_data`` answer with no citations.
"""

from __future__ import annotations

import re

from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk

NO_EVIDENCE_SENTENCE = "The retrieved documents do not contain enough information to answer this."
SNIPPET_MAX = 300

# "[3]" or "[1, 2]" not glued to a word or another bracket, so code like
# items[0] and years like [2024] are not mistaken for citations. Only spaces
# and tabs before a marker are absorbed, never line breaks.
_MARKER = re.compile(r"[ \t]*(?<![\w\]])\[(\d{1,3}(?:[ \t]*,[ \t]*\d{1,3})*)\]")
_FULL_WIDTH = re.compile(r"[ \t]*【\s*(\d+(?:\s*,\s*\d+)*)\s*】")
# Anything else in those brackets (e.g. a tool name) is not a citation.
_FULL_WIDTH_OTHER = re.compile(r"[ \t]*【[^】]*】")
# Words too common to say which sentence answers the question.
_STOPWORDS = frozenset(
    "a about an and are as at be by did do does for from how i in is it me much my of on or "
    "our tell that the this to was we were what when where which who why with you your".split()
)
_NO_EVIDENCE = re.compile(r"do(?:es)? not contain enough information", re.IGNORECASE)


def strip_invalid_markers(text: str, passage_count: int) -> tuple[str, set[int]]:
    """Drop citation numbers outside 1..passage_count; return the text and the valid numbers used."""
    cited: set[int] = set()
    text = _FULL_WIDTH.sub(r" [\1]", text)
    text = _FULL_WIDTH_OTHER.sub("", text)

    def _replace(match: re.Match[str]) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        valid = [n for n in numbers if 1 <= n <= passage_count]
        cited.update(valid)
        if not valid:
            return ""
        leading = match.group(0)[: len(match.group(0)) - len(match.group(0).lstrip())]
        return f"{leading}[{', '.join(str(n) for n in valid)}]"

    return _MARKER.sub(_replace, text), cited


def best_excerpt(content: str, query: str, limit: int = SNIPPET_MAX) -> str:
    """The sentence of ``content`` sharing the most words with ``query``."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", content.strip()) if s.strip()]
    if not sentences:
        return content.strip()[:limit]
    terms = {t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query)} - _STOPWORDS
    excerpt = max(
        sentences,
        key=lambda s: sum(1 for t in re.findall(r"[A-Za-z0-9_]+", s.lower()) if t in terms),
    ).strip()
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[: limit - 3].rstrip() + "..."


def claims_by_citation(text: str) -> dict[int, str]:
    """For each passage number, the answer sentences that cite it, joined."""
    claims: dict[int, list[str]] = {}
    for sentence in re.split(r"(?<=[.!?\]])\s+|\n+", text):
        for group in _MARKER.findall(sentence):
            for n in re.findall(r"\d+", group):
                claims.setdefault(int(n), []).append(_MARKER.sub("", sentence))
    return {n: " ".join(parts) for n, parts in claims.items()}


def no_data_response(request: QueryRequest, chunks: list[RetrievedChunk]) -> QueryResponse:
    return QueryResponse(
        status="no_data",
        question=request.question,
        answer_markdown="## Answer\n\n" + NO_EVIDENCE_SENTENCE,
        citations=[],
        retrieved_chunks=chunks,
    )


def finalize_answer(request: QueryRequest, chunks: list[RetrievedChunk], body: str) -> QueryResponse:
    text, cited = strip_invalid_markers(body.strip(), len(chunks))
    if not cited and _NO_EVIDENCE.search(text):
        return no_data_response(request, chunks)

    query = request.retrieval_text
    claims = claims_by_citation(text)
    citations = [
        chunk.source_reference(
            # Quote the passage sentence closest to the claim that cites it.
            snippet=best_excerpt(chunk.content, f"{claims.get(i, '')} {query}"),
            cited=(i in cited),
        )
        for i, chunk in enumerate(chunks, start=1)
    ]
    listed = [i for i in range(1, len(chunks) + 1) if i in cited] or list(range(1, len(chunks) + 1))
    sources = [f"{i}. {citations[i - 1].citation_label(i)}" for i in listed]
    return QueryResponse(
        status="answered",
        question=request.question,
        answer_markdown="## Answer\n\n" + text + "\n\n## Sources\n\n" + "\n".join(sources),
        citations=citations,
        retrieved_chunks=chunks,
    )
