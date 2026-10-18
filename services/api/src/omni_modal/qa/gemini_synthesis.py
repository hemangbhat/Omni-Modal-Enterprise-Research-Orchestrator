"""Citation-grounded answer generation via Google Gemini.

``GeminiAnswerSynthesizer`` implements the same ``synthesize`` /
``synthesize_stream`` contract as ``ExtractiveAnswerSynthesizer``; the prompt,
citation checks and fallback live in ``omni_modal.qa.grounded``.

Honesty / safety:
- Uses only the Python standard library (``urllib``) — no new dependency.
- On ANY failure (network error, bad response) before the first token it falls
  back to the extractive synthesizer, so the query path never breaks and the
  offline demo is unaffected.
- ``select_answer_synthesizer()`` returns Groq, then Gemini, only when the
  matching key is set and ``LLM_ANSWER_GENERATION_ENABLED`` is not "false";
  otherwise extractive.
"""

from __future__ import annotations

import os

from omni_modal.observability import observability
from omni_modal.qa.grounded import GroundedLlmSynthesizer
from omni_modal.qa.llm import GeminiChatClient, llm_enabled
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer


class GeminiAnswerSynthesizer(GroundedLlmSynthesizer):
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-flash-latest",
        timeout: float = 30.0,
        fallback: ExtractiveAnswerSynthesizer | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiAnswerSynthesizer requires an API key.")
        super().__init__(GeminiChatClient(api_key=api_key, model=model, timeout=timeout), fallback)


def select_answer_synthesizer():
    """Return the best available synthesizer: Groq → Gemini → extractive."""
    if not llm_enabled():
        return ExtractiveAnswerSynthesizer()

    # Groq (free tier, fast, OpenAI-compat)
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            from omni_modal.qa.groq_synthesis import GroqAnswerSynthesizer  # noqa: PLC0415

            return GroqAnswerSynthesizer(
                api_key=groq_key,
                model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
            )
        except Exception as exc:  # pragma: no cover
            observability.capture_message(
                f"GROQ_API_KEY set but Groq synthesizer unavailable ({exc}); trying Gemini.",
                operation="qa.groq.select", level="warning",
            )

    # Gemini (free tier with quota, or paid)
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            return GeminiAnswerSynthesizer(
                api_key=gemini_key,
                model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
            )
        except Exception as exc:  # pragma: no cover
            observability.capture_message(
                f"GEMINI_API_KEY set but Gemini synthesizer unavailable ({exc}); using extractive.",
                operation="qa.gemini.select", level="warning",
            )

    return ExtractiveAnswerSynthesizer()
