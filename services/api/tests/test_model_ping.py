"""The readiness model check asks whether the model exists; it never generates text."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

import _path  # noqa: F401

from omni_modal.qa.llm import GeminiChatClient, GroqChatClient


def _resp(payload: dict):
    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    return _R(json.dumps(payload).encode())


class PingTests(unittest.TestCase):
    def test_groq_lists_models_without_generating(self) -> None:
        seen = []

        def opener(req, timeout):
            seen.append((req.get_method(), req.full_url))
            return _resp({"data": [{"id": "openai/gpt-oss-120b"}, {"id": "other"}]})

        with mock.patch("urllib.request.urlopen", opener):
            GroqChatClient(api_key="k", model="openai/gpt-oss-120b").ping()
            with self.assertRaises(LookupError):
                GroqChatClient(api_key="k", model="llama-3.3-70b-versatile").ping()
        self.assertEqual(seen[0], ("GET", "https://api.groq.com/openai/v1/models"))

    def test_gemini_gets_the_model(self) -> None:
        seen = []

        def opener(req, timeout):
            seen.append((req.get_method(), req.full_url, req.headers.get("X-goog-api-key")))
            return _resp({"name": "models/gemini-flash-latest"})

        with mock.patch("urllib.request.urlopen", opener):
            GeminiChatClient(api_key="g", model="gemini-flash-latest").ping()
        self.assertEqual(seen[0], ("GET", "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest", "g"))

    def test_status_text_names_the_problem(self) -> None:
        import importlib  # noqa: PLC0415

        app_module = importlib.import_module("omni_modal.api.app")

        class _Client:
            name = "groq"

            def __init__(self, exc):
                self.exc = exc

            def ping(self):
                if self.exc:
                    raise self.exc

        cases = [
            (None, "ok (groq)"),
            (urllib.error.HTTPError("u", 401, "x", {}, None), "groq answered HTTP 401"),
            (LookupError("no"), "model name is not available"),
        ]
        for exc, expected in cases:
            with mock.patch.object(app_module, "select_chat_client", lambda exc=exc: _Client(exc)):
                app_module._refresh_model_status()
            self.assertIn(expected, app_module._model_status_cache["value"])


if __name__ == "__main__":
    unittest.main()
