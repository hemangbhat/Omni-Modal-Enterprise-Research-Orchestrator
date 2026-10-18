"""Citation-grounded answer generation via Groq (free tier, OpenAI-compat API).

``GroqAnswerSynthesizer`` implements the same ``synthesize(request, chunks)``
and ``synthesize_stream(request, chunks)`` contract as
``ExtractiveAnswerSynthesizer`` and ``GeminiAnswerSynthesizer``; the prompt,
citation checks and fallback live in ``omni_modal.qa.grounded``.

Selected models (all free-tier, fast):
  openai/gpt-oss-120b  — best quality, 128k context (default)
  openai/gpt-oss-20b   — faster, good quality

``select_answer_synthesizer()`` in ``gemini_synthesis.py`` tries Groq first
(when ``GROQ_API_KEY`` is set), then Gemini, then extractive.
"""

from __future__ import annotations

from omni_modal.qa.grounded import GroundedLlmSynthesizer
from omni_modal.qa.llm import GroqChatClient
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer


class GroqAnswerSynthesizer(GroundedLlmSynthesizer):
    provider = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "openai/gpt-oss-120b",
        timeout: float = 30.0,
        fallback: ExtractiveAnswerSynthesizer | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GroqAnswerSynthesizer requires an API key.")
        super().__init__(GroqChatClient(api_key=api_key, model=model, timeout=timeout), fallback)
