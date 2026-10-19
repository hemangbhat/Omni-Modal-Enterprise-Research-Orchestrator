"""Web research for questions the documents cannot answer: redaction, the client, the gates, the answer."""

from __future__ import annotations

import io
import json
import os
import urllib.error
import unittest

import _path  # noqa: F401

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
for _key in ("DATABASE_URL", "REDIS_URL", "GROQ_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "S3_BUCKET"):
    os.environ.pop(_key, None)

from omni_modal.orchestration.adk_workflow import InternalResearchAdkWorkflow  # noqa: E402
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider  # noqa: E402
from omni_modal.qa.in_memory_store import InMemoryChunkRetriever, InMemoryVectorStore, StoredChunk  # noqa: E402
from omni_modal.qa.models import QueryRequest  # noqa: E402
from omni_modal.research.external import (  # noqa: E402
    WEB_HEADING,
    TavilyClient,
    WebResult,
    redact_question,
)

RESULTS = [
    WebResult("Nimbus Cloud pricing", "https://example.com/nimbus", "Nimbus Cloud lists storage at 0.02 dollars per gigabyte."),
    WebResult("Cloud price survey", "https://example.org/survey", "Average cloud storage costs fell 12 percent in 2026."),
]


class FakeWeb:
    name = "fake"

    def __init__(self, results=RESULTS, fail: bool = False) -> None:
        self.results, self.fail, self.queries = results, fail, []

    def search(self, query: str):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("down")
        return self.results


class RedactionTests(unittest.TestCase):
    def test_removes_personal_data(self) -> None:
        text, usable = redact_question(
            "Email priya@halden.example or call +44 20 7946 0958 about account 12345678 at https://intra.net/x pricing",
            [])
        for secret in ("priya@", "7946", "12345678", "intra.net"):
            self.assertNotIn(secret, text)
        self.assertTrue(usable)

    def test_removes_numbers_and_phrases_from_the_documents(self) -> None:
        internal = ["Our hosting contract with Nimbus Cloud costs 0.021 dollars per gigabyte-hour."]
        text, usable = redact_question(
            "Is our hosting contract with Nimbus Cloud at 0.021 cheap compared to market cloud storage prices?",
            internal)
        self.assertNotIn("0.021", text)
        self.assertNotIn("hosting contract with Nimbus", text)
        self.assertIn("market cloud storage prices", text)
        self.assertTrue(usable)

    def test_mostly_private_question_is_not_sent(self) -> None:
        _text, usable = redact_question("What about priya@halden.example?", [])
        self.assertFalse(usable)


def _response(payload: dict):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    return _Resp(json.dumps(payload).encode())


class TavilyClientTests(unittest.TestCase):
    def test_parses_results_and_sends_bearer_key(self) -> None:
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, request.headers.get("Authorization"), json.loads(request.data), timeout))
            return _response({"results": [
                {"title": "A", "url": "https://a.example", "content": "alpha", "score": 0.9},
                {"title": "", "url": "https://b.example", "content": "beta"},
                {"title": "bad", "url": "javascript:alert(1)", "content": "x"},
            ]})

        results = TavilyClient("tvly-test", opener=opener).search("cloud prices")
        self.assertEqual([r.url for r in results], ["https://a.example", "https://b.example"])
        self.assertEqual(results[1].title, "https://b.example")
        url, auth, body, timeout = seen[0]
        self.assertEqual((url, auth, body["query"], body["max_results"], timeout),
                         ("https://api.tavily.com/search", "Bearer tvly-test", "cloud prices", 5, 10.0))

    def test_retries_server_errors_but_not_a_bad_key(self) -> None:
        calls = {"n": 0}

        def failing(code):
            def opener(request, timeout):
                calls["n"] += 1
                raise urllib.error.HTTPError(request.full_url, code, "err", {}, None)
            return opener

        client = TavilyClient("k", opener=failing(401), retries=2)
        with self.assertRaises(RuntimeError):
            client.search("q")
        self.assertEqual(calls["n"], 1)
        calls["n"] = 0
        client = TavilyClient("k", opener=failing(503), retries=2)
        client._open = failing(503)
        import omni_modal.research.external as ext  # noqa: PLC0415

        sleep, ext.time.sleep = ext.time.sleep, lambda s: None
        try:
            with self.assertRaises(RuntimeError):
                client.search("q")
        finally:
            ext.time.sleep = sleep
        self.assertEqual(calls["n"], 3)


def _workflow(web, docs=("The board approved the 2027 hiring plan.",)):
    provider = HashingQueryEmbeddingProvider()
    store = InMemoryVectorStore()
    for i, text in enumerate(docs):
        store.add(StoredChunk("t1", f"d{i}", f"Doc {i}", "pdf", f"d{i}:0", 0, text, provider.embed_query(text)))
    return InternalResearchAdkWorkflow(InMemoryChunkRetriever(provider, store, mode="hybrid"), web_search=web)


class WorkflowTests(unittest.TestCase):
    QUESTION = "What do cloud storage providers charge per gigabyte?"

    def test_answer_from_web_when_documents_cannot_answer(self) -> None:
        web = FakeWeb()
        result = _workflow(web).answer(QueryRequest("t1", "u1", self.QUESTION, external_permit=lambda: None))
        response = result.response
        self.assertEqual(response.status, "answered")
        self.assertIn("do not contain enough information", response.answer_markdown)
        self.assertIn(WEB_HEADING, response.answer_markdown)
        self.assertIn("Searched the web for", response.answer_markdown)
        self.assertTrue(response.citations)
        self.assertTrue(all(c.scope == "external" and c.url and c.url.startswith("https://") for c in response.citations))
        self.assertEqual(web.queries, [self.QUESTION])

    def test_internal_answer_does_not_search(self) -> None:
        web = FakeWeb()
        result = _workflow(web).answer(QueryRequest("t1", "u1", "Did the board approve the hiring plan?",
                                                    external_permit=lambda: None))
        self.assertEqual(web.queries, [])
        self.assertNotIn(WEB_HEADING, result.response.answer_markdown)

    def test_refused_permit_explains_and_does_not_search(self) -> None:
        web = FakeWeb()
        result = _workflow(web).answer(QueryRequest("t1", "u1", self.QUESTION,
                                                    external_permit=lambda: "it is turned off for this workspace."))
        self.assertEqual(web.queries, [])
        self.assertEqual(result.response.status, "no_data")
        self.assertIn("Web search was not used: it is turned off", result.response.answer_markdown)

    def test_no_permit_means_silent(self) -> None:
        web = FakeWeb()
        result = _workflow(web).answer(QueryRequest("t1", "u1", self.QUESTION))
        self.assertEqual(web.queries, [])
        self.assertNotIn("Web search", result.response.answer_markdown)

    def test_failed_search_still_answers(self) -> None:
        result = _workflow(FakeWeb(fail=True)).answer(QueryRequest("t1", "u1", self.QUESTION, external_permit=lambda: None))
        self.assertEqual(result.response.status, "no_data")
        self.assertIn("did not respond", result.response.answer_markdown)

    def test_stream_matches_final_answer(self) -> None:
        from omni_modal.orchestration.adk_workflow import AgentWorkflowResult  # noqa: PLC0415

        parts, final = [], None
        for item in _workflow(FakeWeb()).answer_stream(QueryRequest("t1", "u1", self.QUESTION, external_permit=lambda: None)):
            if isinstance(item, AgentWorkflowResult):
                final = item
            else:
                parts.append(item)
        self.assertEqual("".join(parts), final.response.answer_markdown)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from omni_modal.api.app import create_app  # noqa: PLC0415
        from omni_modal.api.app import state  # noqa: PLC0415

        cls.state = state
        cls.client = TestClient(create_app())
        cls.client.__enter__()
        cls.web = FakeWeb()
        state.web_search = cls.web
        state.workflow = InternalResearchAdkWorkflow(state.retriever, web_search=cls.web)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def _admin(self, email: str) -> tuple[dict, str]:
        reg = self.client.post("/auth/register", json={"email": email, "password": "password123"}).json()
        return {"Authorization": f"Bearer {reg['token']}"}, reg["tenant_id"]

    def _ask(self, auth: dict) -> list[tuple[str, dict]]:
        from test_projects_api import _events  # noqa: PLC0415

        return _events(self.client.post("/query/stream", headers=auth,
                                        json={"question": "What do cloud storage providers charge per gigabyte?"}).text)

    def test_off_by_default_then_plan_gate_then_on(self) -> None:
        auth, tenant = self._admin("web-admin@example.com")
        settings = self.client.get("/workspace/research-settings", headers=auth).json()
        self.assertEqual((settings["external_research_enabled"], settings["plan_includes"]), (False, False))

        done = self._ask(auth)[-1][1]
        self.assertIn("turned off", json.dumps(done))

        on = self.client.put("/workspace/research-settings", headers=auth, json={"external_research_enabled": True})
        self.assertEqual(on.status_code, 200)
        self.assertIn("plan does not include", json.dumps(self._ask(auth)[-1][1]))

        self.state.saas.change_plan(tenant_id=tenant, user_id="x", plan_id="pro")
        before = len(self.web.queries)
        events = self._ask(auth)
        done = events[-1][1]
        self.assertEqual(len(self.web.queries), before + 1)
        session = self.client.get(f"/research/sessions/{events[0][1]['session_id']}", headers=auth).json()
        stored = session["messages"][-1]["citations"]
        self.assertTrue(stored and all(c["scope"] == "external" and c["url"].startswith("https://") for c in stored))
        self.assertEqual(self.client.get("/workspace/research-settings", headers=auth).json()["used_this_month"], 1)
        self.assertIn("answered", json.dumps(done))

    def test_only_admins_change_the_setting(self) -> None:
        import time  # noqa: PLC0415

        from omni_modal.security.auth import _make_jwt, jwt_secret_from_env  # noqa: PLC0415

        _auth, tenant = self._admin("web-owner@example.com")
        researcher = {"Authorization": "Bearer " + _make_jwt(tenant, "r1", ["researcher"], int(time.time()) + 600,
                                                             jwt_secret_from_env())}
        r = self.client.put("/workspace/research-settings", headers=researcher, json={"external_research_enabled": True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/workspace/research-settings", headers=researcher).status_code, 200)

    def test_quota_runs_out(self) -> None:
        auth, tenant = self._admin("web-quota@example.com")
        self.client.put("/workspace/research-settings", headers=auth, json={"external_research_enabled": True})
        self.state.saas.change_plan(tenant_id=tenant, user_id="x", plan_id="pro")
        for _ in range(300):
            self.state.saas.usage.record(tenant, "external_research")
        self.assertIn("allowance is used up", json.dumps(self._ask(auth)[-1][1]))


if __name__ == "__main__":
    unittest.main()
