"""Web research for questions the workspace's documents cannot answer.

Only a redacted copy of the question leaves the system; no document text is
ever sent. Results come back labelled as external, with their own links, and
are kept apart from internal evidence in the answer.

Provider: Tavily (``TAVILY_API_KEY``), chosen because its free plan needs no
card (see docs/superpowers/plans/2026-10-18-m5-external-and-polish.md).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from omni_modal.observability import observability
from omni_modal.qa.lexical import STOPWORDS
from omni_modal.qa.models import QueryRequest, RetrievedChunk, SourceReference

WEB_HEADING = "## Not in your documents — from the web"
REDACTED = "[redacted]"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")
_LONG_DIGITS = re.compile(r"\b\d{6,}\b")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    content: str


class WebSearchClient:
    """Anything with ``name`` and ``search(query) -> list[WebResult]``."""

    name = "web"

    def search(self, query: str) -> list[WebResult]:  # pragma: no cover - protocol
        raise NotImplementedError


def redact_question(question: str, internal_texts: list[str]) -> tuple[str, bool]:
    """The question with personal data and anything taken from the documents removed.

    Returns ``(redacted, usable)``; ``usable`` is False when too little of the
    question is left to search for, in which case nothing should be sent.
    """
    text = _EMAIL.sub(REDACTED, question)
    text = _URL.sub(REDACTED, text)
    text = _PHONE.sub(REDACTED, text)
    text = _LONG_DIGITS.sub(REDACTED, text)

    corpus = " ".join(internal_texts).lower()
    corpus_numbers = set(_NUMBER.findall(corpus))
    text = _NUMBER.sub(lambda m: REDACTED if m.group(0) in corpus_numbers else m.group(0), text)

    # Any run of four words that also appears in a retrieved passage is document text.
    words = list(_WORD.finditer(text))
    spans: list[tuple[int, int]] = []
    normalised_corpus = " " + " ".join(_WORD.findall(corpus)) + " "
    for i in range(len(words) - 3):
        phrase = " ".join(w.group(0).lower() for w in words[i:i + 4])
        if f" {phrase} " in normalised_corpus:
            spans.append((words[i].start(), words[i + 3].end()))
    for start, end in sorted(_merge(spans), reverse=True):
        text = text[:start] + REDACTED + text[end:]

    text = re.sub(r"(?:\[redacted\]\s*)+", REDACTED + " ", text).strip()
    content = [w for w in _WORD.findall(text.replace(REDACTED, " ")) if w.lower() not in STOPWORDS]
    return text, len(content) >= 2


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class TavilyClient(WebSearchClient):
    name = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str, *, timeout: float = 10.0, retries: int = 2, max_results: int = 5,
                 opener: Callable | None = None) -> None:
        self._key = api_key
        self._timeout = timeout
        self._retries = retries
        self._max_results = max_results
        self._open = opener or urllib.request.urlopen

    def search(self, query: str) -> list[WebResult]:
        body = json.dumps({"query": query, "search_depth": "basic", "max_results": self._max_results}).encode()
        request = urllib.request.Request(self.ENDPOINT, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self._key}"})
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                with self._open(request, timeout=self._timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return _parse_results(payload)[: self._max_results]
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code < 500 and exc.code != 429:
                    break  # a bad key or request will not fix itself
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last = exc
            if attempt < self._retries:
                time.sleep(0.5 * (2 ** attempt))
        raise RuntimeError(f"Web search failed: {type(last).__name__}") from last


def _parse_results(payload: object) -> list[WebResult]:
    results = payload.get("results") if isinstance(payload, dict) else None
    out = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        url, title, content = item.get("url"), item.get("title"), item.get("content")
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            continue
        out.append(WebResult(
            title=(title if isinstance(title, str) and title.strip() else url).strip()[:200],
            url=url,
            content=(content if isinstance(content, str) else "").strip()[:1200],
        ))
    return out


def select_web_search() -> WebSearchClient | None:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    return TavilyClient(key) if key else None


def as_chunks(results: list[WebResult]) -> list[RetrievedChunk]:
    """Web results in the shape the answer synthesizers read (marked as web)."""
    return [
        RetrievedChunk(chunk_id=f"web:{i}", document_id=r.url, title=r.title, source_type="web",
                       chunk_index=i, content=r.content, similarity=0.0, metadata={"url": r.url})
        for i, r in enumerate(results)
    ]


def external_citations(response_citations: list[SourceReference], results: list[WebResult]) -> list[SourceReference]:
    """The synthesizer's citations relabelled as external, with their links."""
    by_url = {r.url: r for r in results}
    out = []
    for c in response_citations:
        result = by_url.get(c.document_id)
        out.append(SourceReference(
            document_id=c.document_id, chunk_id=c.chunk_id, title=c.title, source_type="web",
            chunk_index=c.chunk_index, snippet=c.snippet or (result.content[:300] if result else ""),
            scope="external", cited=c.cited, url=result.url if result else c.document_id,
        ))
    return out


def web_request(request: QueryRequest, query: str) -> QueryRequest:
    """The request the synthesizer sees for web results: no history, the redacted question."""
    from dataclasses import replace  # noqa: PLC0415

    return replace(request, question=query, history=(), search_query=None, document_ids=None)


def log_failure(exc: Exception, tenant_id: str) -> None:
    observability.capture_exception(exc, operation="research.web_search", context={"tenant_id": tenant_id})
