"""One chat interface over the free-tier LLM providers (Groq, then Gemini).

``complete`` returns the whole reply; ``stream`` yields text as the provider
sends it (server-sent events), so callers can forward real tokens instead of
slicing a finished answer. Both use only stdlib ``urllib``.

Messages are ``{"role": "user" | "assistant", "content": str}`` dicts; the
system instruction is passed separately because the providers place it
differently.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Iterator
from typing import Protocol

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}"
# Finish reasons that mean the text was cut off rather than completed.
_GEMINI_CUT_OFF = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"})


class ChatClient(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 1024) -> str: ...

    def stream(self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 1024) -> Iterator[str]: ...


def _sse_data(response) -> Iterator[dict]:
    """Parse ``data: {...}`` lines from an SSE HTTP response."""
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


class GroqChatClient:
    name = "groq"

    def __init__(self, *, api_key: str, model: str = "openai/gpt-oss-120b", timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("GroqChatClient requires an API key.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def _request(self, system: str, messages: list[dict[str, str]], max_tokens: int, stream: bool):
        body = json.dumps({
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "stream": stream,
        }).encode("utf-8")
        return urllib.request.Request(
            GROQ_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "omero-api/1.0",
            },
            method="POST",
        )

    def ping(self) -> None:
        """Check the key works and the model exists, without generating anything.

        (A tiny completion is not a fair test: reasoning models spend a small
        token budget thinking and return nothing.)
        """
        req = urllib.request.Request(GROQ_MODELS_URL, method="GET", headers={
            "Authorization": f"Bearer {self._api_key}", "User-Agent": "omero-api/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
        if self._model not in ids:
            raise LookupError(f"Groq has no model named {self._model!r}.")

    def complete(self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 1024) -> str:
        req = self._request(system, messages, max_tokens, stream=False)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            raise RuntimeError("Groq returned an empty response.")
        return text

    def stream(self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 1024) -> Iterator[str]:
        req = self._request(system, messages, max_tokens, stream=True)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            for event in _sse_data(resp):
                if event.get("error"):
                    raise RuntimeError(f"Groq stream error: {event['error']}")
                delta = (event.get("choices") or [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield delta


class GeminiChatClient:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str = "gemini-flash-latest", timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("GeminiChatClient requires an API key.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def _request(self, system: str, messages: list[dict[str, str]], max_tokens: int, method: str):
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ]
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
        }).encode("utf-8")
        # The key goes in a header, not the URL, so it never lands in access logs.
        return urllib.request.Request(
            GEMINI_URL.format(model=self._model, method=method),
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
            method="POST",
        )

    def ping(self) -> None:
        """Check the key works and the model exists, without generating anything."""
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}",
            method="GET", headers={"x-goog-api-key": self._api_key},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()

    @staticmethod
    def _text(event: dict) -> str:
        candidates = event.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)

    def complete(self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 1024) -> str:
        req = self._request(system, messages, max_tokens, "generateContent")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = self._text(data).strip()
        if not text:
            raise RuntimeError("Gemini returned an empty answer.")
        return text

    def stream(self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 1024) -> Iterator[str]:
        req = self._request(system, messages, max_tokens, "streamGenerateContent?alt=sse")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            for event in _sse_data(resp):
                text = self._text(event)
                if text:
                    yield text
                reason = ((event.get("candidates") or [{}])[0]).get("finishReason")
                if reason in _GEMINI_CUT_OFF:
                    raise RuntimeError(f"Gemini stopped the answer early ({reason}).")


def llm_enabled() -> bool:
    return os.environ.get("LLM_ANSWER_GENERATION_ENABLED", "true").lower() != "false"


def select_chat_client() -> ChatClient | None:
    """Groq when ``GROQ_API_KEY`` is set, else Gemini, else ``None``."""
    if not llm_enabled():
        return None
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        return GroqChatClient(api_key=groq_key, model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"))
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        return GeminiChatClient(api_key=gemini_key, model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"))
    return None
