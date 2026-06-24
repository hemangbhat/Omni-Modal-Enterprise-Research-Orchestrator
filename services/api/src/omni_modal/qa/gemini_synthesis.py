"""Citation-grounded answer generation via Google Gemini (Phase F).

``GeminiAnswerSynthesizer`` implements the same ``synthesize(request, chunks)``
contract as ``ExtractiveAnswerSynthesizer``. It builds a strictly-grounded
prompt from the retrieved chunks (numbered [1..N]), asks Gemini to answer using
only that context with inline [n] citations, and returns a ``QueryResponse``
whose citations map to the chunks actually retrieved.

Honesty / safety:
- Uses only the Python standard library (``urllib``) — no new dependency.
- On ANY failure (missing key, network error, bad response) it falls back to
  the extractive synthesizer, so the query path never breaks and the offline
  demo is unaffected.
- ``select_answer_synthesizer()`` returns Gemini only when ``GEMINI_API_KEY`` is
  set and ``LLM_ANSWER_GENERATION_ENABLED`` is not "false"; otherwise extractive.
"""

from __future__ import annotations

import json
import os
import urllib.request

from omni_modal.observability import observability
from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

_SYSTEM_INSTRUCTION = (
    "You are an enterprise research assistant. Answer the user's question using "
    "ONLY the numbered context passages provided. Cite every claim with the "
    "passage number in square brackets, e.g. [1] or [2]. If the context does "
    "not contain the answer, say exactly: 'The retrieved documents do not "
    "contain enough information to answer this.' Do not use outside knowledge. "
    "Be concise and factual. Format the answer in Markdown."
)


class GeminiAnswerSynthesizer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout: float = 30.0,
        fallback: ExtractiveAnswerSynthesizer | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiAnswerSynthesizer requires an API key.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._fallback = fallback or ExtractiveAnswerSynthesizer()

    def synthesize(self, request: QueryRequest, chunks: list[RetrievedChunk]) -> QueryResponse:
        if not chunks:
            # Identical no-data behaviour to the extractive path.
            return self._fallback.synthesize(request, chunks)

        citations = [chunk.source_reference() for chunk in chunks]
        try:
            answer_body = self._generate(request.question, chunks)
        except Exception as exc:
            observability.capture_message(
                f"Gemini answer generation failed ({exc}); using extractive fallback.",
                operation="qa.gemini.generate",
                level="warning",
            )
            return self._fallback.synthesize(request, chunks)

        sources = [
            f"{index}. {citation.citation_label(index)}"
            for index, citation in enumerate(citations, start=1)
        ]
        answer_markdown = (
            "## Answer\n\n"
            + answer_body.strip()
            + "\n\n## Sources\n\n"
            + "\n".join(sources)
        )
        return QueryResponse(
            status="answered",
            question=request.question,
            answer_markdown=answer_markdown,
            citations=citations,
            retrieved_chunks=chunks,
        )

    def _build_prompt(self, question: str, chunks: list[RetrievedChunk]) -> str:
        context_blocks = []
        for index, chunk in enumerate(chunks, start=1):
            context_blocks.append(f"[{index}] (from \"{chunk.title}\")\n{chunk.content}")
        context = "\n\n".join(context_blocks)
        return (
            f"{_SYSTEM_INSTRUCTION}\n\n"
            f"=== CONTEXT PASSAGES ===\n{context}\n\n"
            f"=== QUESTION ===\n{question}\n\n"
            f"Answer (with [n] citations):"
        )

    def _generate(self, question: str, chunks: list[RetrievedChunk]) -> str:
        prompt = self._build_prompt(question, chunks)
        url = _GEMINI_ENDPOINT.format(model=self._model) + f"?key={self._api_key}"
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini returned no candidates.")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise RuntimeError("Gemini returned an empty answer.")
        return text


def select_answer_synthesizer():
    """Return the Gemini synthesizer when configured + enabled, else extractive."""
    enabled = os.environ.get("LLM_ANSWER_GENERATION_ENABLED", "true").lower() != "false"
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if enabled and api_key:
        try:
            return GeminiAnswerSynthesizer(
                api_key=api_key,
                model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            )
        except Exception as exc:  # pragma: no cover - defensive
            observability.capture_message(
                f"GEMINI_API_KEY set but synthesizer unavailable ({exc}); using extractive.",
                operation="qa.gemini.select",
                level="warning",
            )
    return ExtractiveAnswerSynthesizer()
