"""Unit tests for the Gemini grounded-answer synthesizer (Phase F).

The Gemini HTTP call is mocked, so these verify the prompt construction,
response parsing, citation formatting, and — critically — the graceful
fallback to extractive synthesis on failure, without depending on live API
quota or network access.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock

import _path  # noqa: F401

from omni_modal.qa.gemini_synthesis import GeminiAnswerSynthesizer, select_answer_synthesizer
from omni_modal.qa.models import QueryRequest, RetrievedChunk
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer


def _chunks():
    return [
        RetrievedChunk(chunk_id="c1", document_id="d1", title="Q3 Report", source_type="pdf",
                       chunk_index=0, content="Revenue grew 18% on enterprise subscriptions.",
                       similarity=0.8, metadata={}),
        RetrievedChunk(chunk_id="c2", document_id="d1", title="Q3 Report", source_type="pdf",
                       chunk_index=1, content="Headcount rose to 145.", similarity=0.5, metadata={}),
    ]


def _request():
    return QueryRequest(tenant_id="t1", user_id="u1", question="How much did revenue grow?",
                        top_k=5, min_similarity=0.0)


def _fake_response(text: str):
    payload = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class GeminiSynthesisTests(unittest.TestCase):
    def test_successful_generation_is_grounded_with_citations(self) -> None:
        synth = GeminiAnswerSynthesizer(api_key="k", model="gemini-2.0-flash")
        fake = _fake_response("Revenue grew 18% driven by enterprise subscriptions [1].")
        with mock.patch("urllib.request.urlopen", return_value=fake) as urlopen:
            resp = synth.synthesize(_request(), _chunks())
        self.assertTrue(urlopen.called)
        self.assertEqual(resp.status, "answered")
        self.assertIn("18%", resp.answer_markdown)
        self.assertIn("[1]", resp.answer_markdown)
        self.assertIn("## Sources", resp.answer_markdown)
        # Must NOT be the extractive template.
        self.assertNotIn("Based only on the retrieved internal documents", resp.answer_markdown)
        self.assertEqual(len(resp.citations), 2)

    def test_prompt_contains_numbered_context_and_instruction(self) -> None:
        synth = GeminiAnswerSynthesizer(api_key="k")
        prompt = synth._build_prompt("How much did revenue grow?", _chunks())
        self.assertIn("[1]", prompt)
        self.assertIn("[2]", prompt)
        self.assertIn("Revenue grew 18%", prompt)
        self.assertIn("ONLY the numbered context", prompt)

    def test_http_failure_falls_back_to_extractive(self) -> None:
        synth = GeminiAnswerSynthesizer(api_key="k")
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            resp = synth.synthesize(_request(), _chunks())
        # Extractive fallback still answers with sources.
        self.assertEqual(resp.status, "answered")
        self.assertIn("Based only on the retrieved internal documents", resp.answer_markdown)
        self.assertEqual(len(resp.citations), 2)

    def test_empty_candidates_falls_back(self) -> None:
        synth = GeminiAnswerSynthesizer(api_key="k")
        empty = io.BytesIO(json.dumps({"candidates": []}).encode("utf-8"))
        with mock.patch("urllib.request.urlopen", return_value=empty):
            resp = synth.synthesize(_request(), _chunks())
        self.assertEqual(resp.status, "answered")
        self.assertIn("Based only on the retrieved internal documents", resp.answer_markdown)

    def test_no_chunks_returns_no_data(self) -> None:
        synth = GeminiAnswerSynthesizer(api_key="k")
        resp = synth.synthesize(_request(), [])
        self.assertEqual(resp.status, "no_data")

    def test_select_returns_extractive_without_key(self) -> None:
        saved_groq = os.environ.pop("GROQ_API_KEY", None)
        saved_gemini = os.environ.pop("GEMINI_API_KEY", None)
        try:
            self.assertIsInstance(select_answer_synthesizer(), ExtractiveAnswerSynthesizer)
        finally:
            if saved_groq is not None:
                os.environ["GROQ_API_KEY"] = saved_groq
            if saved_gemini is not None:
                os.environ["GEMINI_API_KEY"] = saved_gemini

    def test_select_returns_extractive_when_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k", "LLM_ANSWER_GENERATION_ENABLED": "false"}):
            self.assertIsInstance(select_answer_synthesizer(), ExtractiveAnswerSynthesizer)

    def test_select_returns_gemini_when_no_groq_key(self) -> None:
        env = {"GEMINI_API_KEY": "k", "LLM_ANSWER_GENERATION_ENABLED": "true"}
        env.pop("GROQ_API_KEY", None)
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("GROQ_API_KEY", None)
            result = select_answer_synthesizer()
        self.assertIsInstance(result, GeminiAnswerSynthesizer)

    def test_select_returns_groq_when_groq_key_set(self) -> None:
        from omni_modal.qa.groq_synthesis import GroqAnswerSynthesizer
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test", "LLM_ANSWER_GENERATION_ENABLED": "true"}):
            self.assertIsInstance(select_answer_synthesizer(), GroqAnswerSynthesizer)


if __name__ == "__main__":
    unittest.main()
