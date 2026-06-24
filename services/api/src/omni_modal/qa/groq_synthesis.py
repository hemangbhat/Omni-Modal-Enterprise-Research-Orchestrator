"""Citation-grounded answer generation via Groq (free tier, OpenAI-compat API).

``GroqAnswerSynthesizer`` implements the same ``synthesize(request, chunks)``
contract as ``ExtractiveAnswerSynthesizer`` and ``GeminiAnswerSynthesizer``.
Groq exposes an OpenAI-compatible chat completions endpoint, so the
implementation uses stdlib ``urllib`` with no new dependency.

Selected models (all free-tier, fast):
  llama-3.3-70b-versatile  — best quality, 128k context
  llama-3.1-8b-instant     — fastest, good quality
  gemma2-9b-it             — strong at instruction-following

``select_answer_synthesizer()`` in ``gemini_synthesis.py`` now tries Groq
first (when ``GROQ_API_KEY`` is set), then Gemini, then extractive.
"""

from __future__ import annotations

import json
import os
import urllib.request

from omni_modal.observability import observability
from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_SYSTEM = (
    "You are an enterprise research assistant. Answer the user's question using "
    "ONLY the numbered context passages provided. Cite every claim inline with "
    "the passage number in square brackets, e.g. [1] or [2]. If the context "
    "does not contain enough information, reply exactly: "
    "'The retrieved documents do not contain enough information to answer this.' "
    "Do not use outside knowledge. Be concise and factual. Format in Markdown."
)


class GroqAnswerSynthesizer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        timeout: float = 30.0,
        fallback: ExtractiveAnswerSynthesizer | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GroqAnswerSynthesizer requires an API key.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._fallback = fallback or ExtractiveAnswerSynthesizer()

    def synthesize(self, request: QueryRequest, chunks: list[RetrievedChunk]) -> QueryResponse:
        if not chunks:
            return self._fallback.synthesize(request, chunks)
        citations = [chunk.source_reference() for chunk in chunks]
        try:
            answer_body = self._generate(request.question, chunks)
        except Exception as exc:
            observability.capture_message(
                f"Groq answer generation failed ({exc}); using extractive fallback.",
                operation="qa.groq.generate",
                level="warning",
            )
            return self._fallback.synthesize(request, chunks)
        sources = [
            f"{i}. {citation.citation_label(i)}"
            for i, citation in enumerate(citations, start=1)
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

    def _build_user_message(self, question: str, chunks: list[RetrievedChunk]) -> str:
        context_blocks = []
        for i, chunk in enumerate(chunks, start=1):
            context_blocks.append(f'[{i}] (from "{chunk.title}")\n{chunk.content}')
        context = "\n\n".join(context_blocks)
        return (
            f"=== CONTEXT PASSAGES ===\n{context}\n\n"
            f"=== QUESTION ===\n{question}\n\n"
            f"Answer with inline [n] citations:"
        )

    def _generate(self, question: str, chunks: list[RetrievedChunk]) -> str:
        body = json.dumps({
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": self._build_user_message(question, chunks)},
            ],
            "temperature": 0.2,
            "max_tokens": 1024,
        }).encode("utf-8")
        req = urllib.request.Request(
            _GROQ_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "omero-api/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            raise RuntimeError("Groq returned an empty response.")
        return text
