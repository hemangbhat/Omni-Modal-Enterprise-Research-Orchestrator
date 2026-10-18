"""HTTP tests for saved research sessions, follow-ups and the assistant.

In-process (TestClient), no database and no LLM key, so answers come from the
extractive synthesizer and the assistant reports that no model is configured.
"""

from __future__ import annotations

import json
import os
import unittest

import _path  # noqa: F401

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
for _key in ("DATABASE_URL", "GEMINI_API_KEY", "GROQ_API_KEY"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.api.app import state as app_state  # noqa: E402
from omni_modal.qa.embedding_factory import select_embedding_provider  # noqa: E402
from omni_modal.qa.in_memory_store import StoredChunk  # noqa: E402


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((name, json.loads(data)))
    return out


class ResearchApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def _user(self, email: str, seed: bool = True) -> dict:
        reg = self.client.post("/auth/register", json={"email": email, "password": "password123"}).json()
        if seed:
            # The embedder the app itself selected (env may change its dimensions).
            provider = select_embedding_provider().provider
            for i, text in enumerate(["Revenue grew 18% in Q3.", "Headcount rose to 145 in Q3."]):
                app_state.vector_store.add(StoredChunk(
                    tenant_id=reg["tenant_id"], document_id="doc-1", title="Q3 Board Minutes",
                    source_type="pdf", chunk_id=f"doc-1-{i}", chunk_index=i, content=text,
                    embedding=provider.embed_query(text), metadata={"pageNumber": i + 1},
                ))
        return {"Authorization": f"Bearer {reg['token']}"}

    def _ask(self, auth: dict, question: str, session_id: str | None = None):
        body = {"question": question}
        if session_id:
            body["session_id"] = session_id
        return self.client.post("/query/stream", json=body, headers=auth)

    def test_first_question_creates_a_saved_session(self) -> None:
        auth = self._user("rs-first@example.com")
        r = self._ask(auth, "How much did revenue grow?")
        self.assertEqual(r.status_code, 200, r.text)
        events = _events(r.text)
        self.assertEqual(events[0][0], "session")
        session_id = events[0][1]["session_id"]
        done = events[-1][1]
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(done["session_id"], session_id)
        self.assertEqual(done["status"], "answered")
        self.assertTrue(done["citations"][0]["snippet"])
        self.assertEqual(done["citations"][0]["scope"], "internal")
        self.assertEqual(done["citations"][0]["page_number"], 1)
        streamed = "".join(e[1]["delta"] for e in events if e[0] == "delta")
        self.assertEqual(streamed, done["answer_markdown"])

        detail = self.client.get(f"/research/sessions/{session_id}", headers=auth).json()
        self.assertEqual(detail["title"], "How much did revenue grow?")
        self.assertEqual([m["role"] for m in detail["messages"]], ["user", "assistant"])
        self.assertEqual(detail["messages"][1]["content"], done["answer_markdown"])
        self.assertTrue(detail["messages"][1]["citations"])

    def test_follow_up_uses_the_earlier_question_for_retrieval(self) -> None:
        auth = self._user("rs-follow@example.com")
        first = _events(self._ask(auth, "What was Q3 revenue growth?").text)
        session_id = first[0][1]["session_id"]
        second = _events(self._ask(auth, "and headcount?", session_id).text)
        self.assertEqual(second[0][1]["session_id"], session_id)
        self.assertEqual(second[-1][1]["search_query"], "What was Q3 revenue growth? and headcount?")
        detail = self.client.get(f"/research/sessions/{session_id}", headers=auth).json()
        self.assertEqual(len(detail["messages"]), 4)

    def test_a_stream_that_breaks_midway_keeps_the_turn(self) -> None:
        from unittest import mock

        auth = self._user("rs-broken@example.com")

        def broken(_req):
            yield "## Answer\n\nRevenue grew"
            raise RuntimeError("model connection dropped")

        with mock.patch.object(app_state.workflow, "answer_stream", side_effect=broken):
            events = _events(self._ask(auth, "How much did revenue grow?").text)
        self.assertEqual(events[-1][0], "error")
        sid = events[0][1]["session_id"]
        messages = self.client.get(f"/research/sessions/{sid}", headers=auth).json()["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["status"], "interrupted")
        self.assertIn("Revenue grew", messages[1]["content"])

    def test_list_rename_delete(self) -> None:
        auth = self._user("rs-crud@example.com", seed=False)
        created = self.client.post("/research/sessions", json={"title": "Pricing"}, headers=auth)
        self.assertEqual(created.status_code, 201, created.text)
        sid = created.json()["id"]
        listed = self.client.get("/research/sessions", headers=auth).json()["sessions"]
        self.assertEqual([s["id"] for s in listed], [sid])
        renamed = self.client.patch(f"/research/sessions/{sid}", json={"title": "Pricing review"}, headers=auth)
        self.assertEqual(renamed.json()["title"], "Pricing review")
        self.assertEqual(self.client.delete(f"/research/sessions/{sid}", headers=auth).status_code, 200)
        self.assertEqual(self.client.get(f"/research/sessions/{sid}", headers=auth).status_code, 404)

    def test_another_users_session_is_not_found(self) -> None:
        owner = self._user("rs-owner@example.com")
        intruder = self._user("rs-intruder@example.com")
        sid = _events(self._ask(owner, "How much did revenue grow?").text)[0][1]["session_id"]
        self.assertEqual(self.client.get(f"/research/sessions/{sid}", headers=intruder).status_code, 404)
        self.assertEqual(
            self.client.patch(f"/research/sessions/{sid}", json={"title": "x"}, headers=intruder).status_code, 404
        )
        self.assertEqual(self.client.delete(f"/research/sessions/{sid}", headers=intruder).status_code, 404)
        self.assertEqual(self._ask(intruder, "continue", sid).status_code, 404)
        self.assertEqual(self.client.get("/research/sessions", headers=intruder).json()["sessions"], [])
        # The owner's session is untouched.
        detail = self.client.get(f"/research/sessions/{sid}", headers=owner).json()
        self.assertEqual(len(detail["messages"]), 2)

    def test_sessions_require_auth(self) -> None:
        self.assertEqual(self.client.get("/research/sessions").status_code, 401)
        self.assertEqual(self.client.post("/assistant/stream", json={"message": "hi"}).status_code, 401)

    def test_assistant_without_a_model_says_so(self) -> None:
        auth = self._user("rs-assist@example.com")
        r = self.client.post(
            "/assistant/stream",
            json={"message": "how many documents do I have?", "page": "/documents"},
            headers=auth,
        )
        self.assertEqual(r.status_code, 200, r.text)
        events = _events(r.text)
        self.assertEqual(events[0][0], "session")
        done = events[-1][1]
        self.assertIn("No AI model is configured", done["answer_markdown"])
        self.assertIn("Q3 Board Minutes", done["answer_markdown"])
        self.assertIn("list_documents", [t["name"] for t in done["tools"]])
        sid = done["session_id"]
        kinds = self.client.get("/research/sessions?kind=assistant", headers=auth).json()["sessions"]
        self.assertEqual([s["id"] for s in kinds], [sid])
        self.assertEqual(self.client.get("/research/sessions?kind=research", headers=auth).json()["sessions"], [])

    def test_assistant_session_cannot_be_continued_as_research(self) -> None:
        auth = self._user("rs-kind@example.com")
        done = _events(self.client.post("/assistant/stream", json={"message": "hi"}, headers=auth).text)[-1][1]
        self.assertEqual(self._ask(auth, "continue", done["session_id"]).status_code, 404)

    def test_assistant_navigation_suggestion(self) -> None:
        auth = self._user("rs-nav@example.com", seed=False)
        done = _events(self.client.post(
            "/assistant/stream", json={"message": "take me to the team page"}, headers=auth
        ).text)[-1][1]
        self.assertEqual(done["actions"], [{"label": "Open Team", "href": "/team"}])


if __name__ == "__main__":
    unittest.main()
