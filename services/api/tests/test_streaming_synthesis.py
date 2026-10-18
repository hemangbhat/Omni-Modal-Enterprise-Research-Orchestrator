"""Real token streaming from the answer synthesizers (provider HTTP is mocked)."""

from __future__ import annotations

import io
import json
import unittest
from unittest import mock

import _path  # noqa: F401

from omni_modal.qa.gemini_synthesis import GeminiAnswerSynthesizer
from omni_modal.qa.groq_synthesis import GroqAnswerSynthesizer
from omni_modal.qa.llm import GeminiChatClient, GroqChatClient
from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer


def _chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id="c1", document_id="d1", title="Q3 Report", source_type="pdf",
                       chunk_index=0, content="Revenue grew 18% on enterprise subscriptions.",
                       similarity=0.8, metadata={"pageNumber": 3}),
        RetrievedChunk(chunk_id="c2", document_id="d1", title="Q3 Report", source_type="pdf",
                       chunk_index=1, content="Headcount rose to 145.", similarity=0.5, metadata={}),
    ]


def _request(**kw) -> QueryRequest:
    return QueryRequest(tenant_id="t1", user_id="u1", question="How much did revenue grow?", **kw)


def _groq_sse(*tokens: str) -> io.BytesIO:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': t}}]})}\n\n" for t in tokens]
    lines.append("data: [DONE]\n\n")
    return io.BytesIO("".join(lines).encode("utf-8"))


def _gemini_sse(*tokens: str) -> io.BytesIO:
    lines = [
        f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': t}]}}]})}\n\n" for t in tokens
    ]
    return io.BytesIO("".join(lines).encode("utf-8"))


def _split(items) -> tuple[list[str], QueryResponse]:
    items = list(items)
    final = items[-1]
    assert isinstance(final, QueryResponse), final
    deltas = items[:-1]
    assert all(isinstance(d, str) for d in deltas)
    return deltas, final


class _FailingStream:
    """An HTTP response that yields some SSE lines, then drops the connection."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def __iter__(self):
        yield from self._lines
        raise ConnectionResetError("connection dropped")


class ExtractiveStreamTests(unittest.TestCase):
    def test_streams_its_finished_text_then_the_response(self) -> None:
        deltas, final = _split(ExtractiveAnswerSynthesizer().synthesize_stream(_request(), _chunks()))
        self.assertEqual("".join(deltas), final.answer_markdown)
        self.assertTrue(all(c.cited for c in final.citations))
        self.assertEqual(final.citations[0].snippet, "Revenue grew 18% on enterprise subscriptions.")

    def test_no_chunks_streams_no_data(self) -> None:
        _, final = _split(ExtractiveAnswerSynthesizer().synthesize_stream(_request(), []))
        self.assertEqual(final.status, "no_data")


class GroqStreamTests(unittest.TestCase):
    def test_yields_provider_tokens_as_they_arrive(self) -> None:
        synth = GroqAnswerSynthesizer(api_key="k")
        with mock.patch("urllib.request.urlopen", return_value=_groq_sse("Revenue grew ", "18% [1].")):
            deltas, final = _split(synth.synthesize_stream(_request(), _chunks()))
        self.assertEqual(deltas[0], "## Answer\n\n")
        self.assertEqual(deltas[1:3], ["Revenue grew ", "18% [1]."])
        self.assertEqual("".join(deltas), final.answer_markdown)
        self.assertIn("## Sources", final.answer_markdown)
        self.assertEqual([c.cited for c in final.citations], [True, False])

    def test_request_asks_for_a_stream_and_carries_history(self) -> None:
        synth = GroqAnswerSynthesizer(api_key="k")
        req = _request(history=(("user", "What were Q3 results?"), ("assistant", "## Answer\n\nGood. [1]\n\n## Sources\n\n1. x")))
        with mock.patch("urllib.request.urlopen", return_value=_groq_sse("ok [1]")) as urlopen:
            list(synth.synthesize_stream(req, _chunks()))
        body = json.loads(urlopen.call_args[0][0].data)
        self.assertTrue(body["stream"])
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(body["messages"][2]["content"], "Good. [1]")
        self.assertIn("How much did revenue grow?", body["messages"][-1]["content"])

    def test_failure_before_any_token_falls_back_to_extractive(self) -> None:
        synth = GroqAnswerSynthesizer(api_key="k")
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            deltas, final = _split(synth.synthesize_stream(_request(), _chunks()))
        self.assertIn("Based only on the retrieved internal documents", final.answer_markdown)
        self.assertEqual("".join(deltas), final.answer_markdown)

    def test_failure_mid_stream_keeps_partial_text_and_says_so(self) -> None:
        line = f"data: {json.dumps({'choices': [{'delta': {'content': 'Revenue grew [1]'}}]})}\n".encode()
        synth = GroqAnswerSynthesizer(api_key="k")
        with mock.patch("urllib.request.urlopen", return_value=_FailingStream([line])):
            deltas, final = _split(synth.synthesize_stream(_request(), _chunks()))
        self.assertIn("Revenue grew [1]", final.answer_markdown)
        self.assertIn("stopped before finishing", final.answer_markdown)
        self.assertEqual(final.status, "answered")

    def test_no_evidence_reply_streams_to_no_data(self) -> None:
        synth = GroqAnswerSynthesizer(api_key="k")
        reply = "The retrieved documents do not contain enough information to answer this."
        with mock.patch("urllib.request.urlopen", return_value=_groq_sse(reply)):
            _, final = _split(synth.synthesize_stream(_request(), _chunks()))
        self.assertEqual(final.status, "no_data")
        self.assertEqual(final.citations, [])


class GeminiStreamTests(unittest.TestCase):
    def test_streams_from_the_sse_endpoint_with_key_in_header(self) -> None:
        synth = GeminiAnswerSynthesizer(api_key="secret-key")
        with mock.patch("urllib.request.urlopen", return_value=_gemini_sse("Revenue ", "grew [1].")) as urlopen:
            deltas, final = _split(synth.synthesize_stream(_request(), _chunks()))
        req = urlopen.call_args[0][0]
        self.assertIn("streamGenerateContent?alt=sse", req.full_url)
        self.assertNotIn("secret-key", req.full_url)
        self.assertEqual(req.get_header("X-goog-api-key"), "secret-key")
        self.assertEqual(deltas[1:3], ["Revenue ", "grew [1]."])
        self.assertEqual(final.status, "answered")


class ChatClientTests(unittest.TestCase):
    def test_groq_complete_and_stream(self) -> None:
        client = GroqChatClient(api_key="k")
        done = io.BytesIO(json.dumps({"choices": [{"message": {"content": " hi "}}]}).encode())
        with mock.patch("urllib.request.urlopen", return_value=done):
            self.assertEqual(client.complete("sys", [{"role": "user", "content": "x"}]), "hi")
        with mock.patch("urllib.request.urlopen", return_value=_groq_sse("a", "b")):
            self.assertEqual(list(client.stream("sys", [{"role": "user", "content": "x"}])), ["a", "b"])

    def test_provider_error_or_safety_stop_mid_stream_raises(self) -> None:
        groq_error = io.BytesIO(
            b'data: {"choices":[{"delta":{"content":"a"}}]}\n\ndata: {"error":{"message":"overloaded"}}\n\n'
        )
        with mock.patch("urllib.request.urlopen", return_value=groq_error):
            with self.assertRaises(RuntimeError):
                list(GroqChatClient(api_key="k").stream("s", [{"role": "user", "content": "x"}]))
        safety = io.BytesIO(
            b'data: {"candidates":[{"content":{"parts":[{"text":"a"}]},"finishReason":"SAFETY"}]}\n\n'
        )
        with mock.patch("urllib.request.urlopen", return_value=safety):
            with self.assertRaises(RuntimeError):
                list(GeminiChatClient(api_key="k").stream("s", [{"role": "user", "content": "x"}]))

    def test_gemini_maps_assistant_role_to_model(self) -> None:
        client = GeminiChatClient(api_key="k")
        with mock.patch("urllib.request.urlopen", return_value=_gemini_sse("ok")) as urlopen:
            list(client.stream("sys", [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]))
        body = json.loads(urlopen.call_args[0][0].data)
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model"])
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "sys")


if __name__ == "__main__":
    unittest.main()
