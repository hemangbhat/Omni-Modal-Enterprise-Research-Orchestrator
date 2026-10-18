"""Citation-grounded answers from an LLM, whole or streamed.

``GroundedLlmSynthesizer`` builds a strictly grounded prompt from the
retrieved passages (numbered [1..N]) plus the earlier turns of the
conversation, asks the model to answer only from those passages, and checks
the reply with ``finalize_answer``. Any provider failure before the first
token falls back to the extractive synthesizer, so the query path never breaks
and the offline demo is unaffected.

``synthesize_stream`` yields text deltas as the provider sends them and then,
last, the final ``QueryResponse``. The final answer is authoritative: it has
invalid citation markers removed, so a client should replace the streamed text
with it.
"""

from __future__ import annotations

from collections.abc import Iterator

from omni_modal.observability import observability
from omni_modal.qa.citations import NO_EVIDENCE_SENTENCE, finalize_answer
from omni_modal.qa.llm import ChatClient
from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer

SYSTEM_PROMPT = (
    "You are an enterprise research assistant. Answer the user's question using "
    "ONLY the numbered context passages provided. Cite every claim inline with "
    "the passage number in square brackets, e.g. [1] or [2]. If the context "
    "does not contain enough information, reply exactly: "
    f"'{NO_EVIDENCE_SENTENCE}' "
    "Earlier conversation turns are given only so you can resolve references "
    "such as 'it' or 'that quarter'; every claim must still come from the "
    "numbered passages. Do not use outside knowledge. Be concise and factual. "
    "Format in Markdown."
)

INTERRUPTED_NOTE = "\n\n_The model stopped before finishing this answer._"
_HISTORY_CHARS = 1500


def _history_text(role: str, content: str) -> str:
    """Strip the answer framing so earlier turns cost fewer tokens."""
    text = content
    if role == "assistant":
        text = text.split("## Sources", 1)[0]
        text = text.replace("## Answer", "", 1)
    return text.strip()[:_HISTORY_CHARS]


def build_messages(request: QueryRequest, chunks: list[RetrievedChunk]) -> list[dict[str, str]]:
    messages = [
        {"role": role, "content": _history_text(role, content)}
        for role, content in request.history
        if role in ("user", "assistant") and content.strip()
    ]
    context = "\n\n".join(
        f'[{i}] (from "{chunk.title}")\n{chunk.content}' for i, chunk in enumerate(chunks, start=1)
    )
    messages.append({
        "role": "user",
        "content": (
            f"=== CONTEXT PASSAGES ===\n{context}\n\n"
            f"=== QUESTION ===\n{request.question}\n\n"
            "Answer with inline [n] citations:"
        ),
    })
    return messages


class GroundedLlmSynthesizer:
    provider = "llm"

    def __init__(self, client: ChatClient, fallback: ExtractiveAnswerSynthesizer | None = None) -> None:
        self._client = client
        self._fallback = fallback or ExtractiveAnswerSynthesizer()

    def _warn(self, exc: Exception) -> None:
        observability.capture_message(
            f"{self.provider} answer generation failed ({exc}); using extractive fallback.",
            operation=f"qa.{self.provider}.generate",
            level="warning",
        )

    def synthesize(self, request: QueryRequest, chunks: list[RetrievedChunk]) -> QueryResponse:
        if not chunks:
            return self._fallback.synthesize(request, chunks)
        try:
            body = self._client.complete(SYSTEM_PROMPT, build_messages(request, chunks))
        except Exception as exc:
            self._warn(exc)
            return self._fallback.synthesize(request, chunks)
        return finalize_answer(request, chunks, body)

    def synthesize_stream(
        self, request: QueryRequest, chunks: list[RetrievedChunk]
    ) -> Iterator[str | QueryResponse]:
        if not chunks:
            yield from self._fallback.synthesize_stream(request, chunks)
            return
        parts: list[str] = []
        try:
            for delta in self._client.stream(SYSTEM_PROMPT, build_messages(request, chunks)):
                if not parts:
                    yield "## Answer\n\n"
                parts.append(delta)
                yield delta
        except Exception as exc:
            self._warn(exc)
            if parts:
                parts.append(INTERRUPTED_NOTE)
                yield INTERRUPTED_NOTE
        if not parts:
            yield from self._fallback.synthesize_stream(request, chunks)
            return

        final = finalize_answer(request, chunks, "".join(parts))
        streamed = "## Answer\n\n" + "".join(parts)
        if final.answer_markdown.startswith(streamed) and len(final.answer_markdown) > len(streamed):
            yield final.answer_markdown[len(streamed):]
        yield final
