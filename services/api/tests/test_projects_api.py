"""HTTP tests for projects: CRUD, links, findings, reports, scoped research, isolation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import _path  # noqa: F401
from _pdf import make_pdf

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
for _key in ("DATABASE_URL", "GEMINI_API_KEY", "GROQ_API_KEY", "S3_BUCKET"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.api.app import state as app_state  # noqa: E402


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


class ProjectsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        os.environ["LOCAL_STORAGE_DIR"] = cls._dir.name
        cls.client = TestClient(create_app())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        os.environ.pop("LOCAL_STORAGE_DIR", None)
        cls._dir.cleanup()

    def _user(self, email: str) -> dict:
        reg = self.client.post("/auth/register", json={"email": email, "password": "password123"}).json()
        return {"Authorization": f"Bearer {reg['token']}"}

    def _upload(self, auth: dict, text: str, name: str) -> str:
        import base64

        r = self.client.post("/ingest/upload", headers=auth, json={
            "filename": name, "content_base64": base64.b64encode(make_pdf(text)).decode(), "title": name})
        self.assertEqual(r.status_code, 202, r.text)
        app_state.dispatcher.wait_idle()
        return r.json()["document_id"]

    def _project(self, auth: dict, name: str = "Q3 review") -> str:
        r = self.client.post("/projects", json={"name": name, "description": "Board prep"}, headers=auth)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def test_research_in_a_project_uses_only_its_documents(self) -> None:
        auth = self._user("proj-scope@example.com")
        inside = self._upload(auth, "Zephyr revenue grew 18 percent in the third quarter.", "inside.pdf")
        self._upload(auth, "Zephyr revenue fell 40 percent in the other division.", "outside.pdf")
        pid = self._project(auth)
        self.assertEqual(self.client.post(f"/projects/{pid}/documents", json={"document_id": inside},
                                          headers=auth).status_code, 200)

        events = _events(self.client.post("/query/stream", headers=auth, json={
            "question": "How did Zephyr revenue change?", "project_id": pid}).text)
        done = events[-1][1]
        self.assertEqual(done["project_id"], pid)
        self.assertEqual({c["document_id"] for c in done["citations"]}, {inside})
        # A follow-up in that session stays inside the project.
        follow = _events(self.client.post("/query/stream", headers=auth, json={
            "question": "and what about the other division?", "session_id": done["session_id"]}).text)[-1][1]
        self.assertEqual({c["document_id"] for c in follow["citations"]}, {inside})
        # The same question without a project sees both documents (and is not served from the scoped cache).
        unscoped = _events(self.client.post("/query/stream", headers=auth, json={
            "question": "How did Zephyr revenue change?"}).text)[-1][1]
        self.assertEqual(len({c["document_id"] for c in unscoped["citations"]}), 2)

    def test_project_detail_findings_and_report(self) -> None:
        auth = self._user("proj-detail@example.com")
        doc = self._upload(auth, "The board approved a budget of 2.4 million dollars.", "minutes.pdf")
        pid = self._project(auth)
        self.client.post(f"/projects/{pid}/documents", json={"document_id": doc}, headers=auth)
        answer = self.client.post("/query", json={"question": "What did the board approve?"}, headers=auth).json()
        finding = self.client.post(f"/projects/{pid}/items", headers=auth, json={
            "kind": "finding", "title": "What did the board approve?",
            "body": answer["answer_markdown"], "citations": answer["citations"]})
        self.assertEqual(finding.status_code, 201, finding.text)

        detail = self.client.get(f"/projects/{pid}", headers=auth).json()
        self.assertEqual([d["document_id"] for d in detail["documents"]], [doc])
        self.assertEqual(detail["items"][0]["kind"], "finding")
        self.assertIn("decision", detail["insights"])
        self.assertEqual(detail["activity"][0]["action"], "finding_added")

        report = self.client.post(f"/projects/{pid}/reports", headers=auth).json()
        self.assertIn("## Findings", report["markdown"])
        self.assertIn("## Sources", report["markdown"])
        download = self.client.get(f"/reports/{report['id']}/download", headers=auth)
        self.assertEqual(download.status_code, 200)
        self.assertIn("text/markdown", download.headers["content-type"])
        self.assertIn('filename="Q3 review report.md"', download.headers["content-disposition"])

    def test_session_report_and_linking(self) -> None:
        auth = self._user("proj-session@example.com")
        self._upload(auth, "Headcount rose to 145 people.", "people.pdf")
        pid = self._project(auth)
        sid = _events(self.client.post("/query/stream", headers=auth,
                                       json={"question": "What was headcount?"}).text)[0][1]["session_id"]
        linked = self.client.put(f"/research/sessions/{sid}/project", json={"project_id": pid}, headers=auth)
        self.assertEqual(linked.json()["project_id"], pid)
        report = self.client.post(f"/research/sessions/{sid}/report", headers=auth)
        self.assertEqual(report.status_code, 201)
        self.assertEqual(report.json()["project_id"], pid)
        self.assertIn("## 1. What was headcount?", report.json()["markdown"])

    def test_auditor_reads_but_cannot_change(self) -> None:
        reg = self.client.post("/auth/register", json={"email": "proj-audit@example.com",
                                                       "password": "password123"}).json()
        owner = {"Authorization": f"Bearer {reg['token']}"}
        pid = self._project(owner)
        import time

        from omni_modal.security.auth import _make_jwt, jwt_secret_from_env

        auditor = {"Authorization": "Bearer " + _make_jwt(reg["tenant_id"], "auditor-1", ["auditor"],
                                                           int(time.time()) + 600, jwt_secret_from_env())}
        self.assertEqual(self.client.get(f"/projects/{pid}", headers=auditor).status_code, 200)
        self.assertEqual(self.client.post("/projects", json={"name": "x"}, headers=auditor).status_code, 403)
        self.assertEqual(self.client.post(f"/projects/{pid}/items", headers=auditor,
                                          json={"kind": "note", "title": "x"}).status_code, 403)
        self.assertEqual(self.client.put("/research/sessions/any/project", headers=auditor,
                                         json={"project_id": pid}).status_code, 403)
        self.assertEqual(self.client.post("/research/sessions/any/report", headers=auditor).status_code, 403)

    def test_report_download_name_with_non_ascii_title(self) -> None:
        auth = self._user("proj-unicode@example.com")
        pid = self._project(auth, name="Résumé review — Q3")
        report = self.client.post(f"/projects/{pid}/reports", headers=auth).json()
        download = self.client.get(f"/reports/{report['id']}/download", headers=auth)
        self.assertEqual(download.status_code, 200)
        header = download.headers["content-disposition"]
        self.assertIn("filename*=UTF-8''R%C3%A9sum%C3%A9%20review%20%E2%80%94%20Q3%20report.md", header)

    def test_dashboard_counts_and_readiness_does_not_call_model(self) -> None:
        auth = self._user("proj-dash@example.com")
        self._upload(auth, "Dashboard document text.", "dash.pdf")
        self._project(auth, name="Dash project")
        body = self.client.get("/dashboard", headers=auth).json()
        self.assertEqual(body["projects"], 1)
        ready = self.client.get("/health/ready")
        self.assertIn("model_provider", ready.json().get("checks", {}))

    def test_other_tenant_cannot_reach_project_or_link_its_documents(self) -> None:
        owner = self._user("proj-owner@example.com")
        intruder = self._user("proj-intruder@example.com")
        pid = self._project(owner)
        their_doc = self._upload(intruder, "Intruder document text here.", "theirs.pdf")
        for method, path, body in (
            ("get", f"/projects/{pid}", None),
            ("patch", f"/projects/{pid}", {"name": "hijack"}),
            ("post", f"/projects/{pid}/archive", None),
            ("post", f"/projects/{pid}/documents", {"document_id": their_doc}),
            ("post", f"/projects/{pid}/items", {"kind": "note", "title": "x"}),
            ("post", f"/projects/{pid}/reports", None),
            ("get", f"/projects/{pid}/search?q=x", None),
        ):
            kwargs = {"headers": intruder}
            if body is not None:
                kwargs["json"] = body
            r = getattr(self.client, method)(path, **kwargs)
            self.assertEqual(r.status_code, 404, f"{method} {path}: {r.status_code}")
        # The owner cannot pull the other tenant's document into their project either.
        r = self.client.post(f"/projects/{pid}/documents", json={"document_id": their_doc}, headers=owner)
        self.assertEqual(r.status_code, 404)
        # Research cannot be scoped to another tenant's project.
        r = self.client.post("/query/stream", json={"question": "x", "project_id": pid}, headers=intruder)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get("/projects", headers=intruder).json()["projects"], [])

    def test_deleted_document_leaves_the_project(self) -> None:
        auth = self._user("proj-delete@example.com")
        doc = self._upload(auth, "Temporary document about logistics.", "temp.pdf")
        pid = self._project(auth)
        self.client.post(f"/projects/{pid}/documents", json={"document_id": doc}, headers=auth)
        self.client.delete(f"/documents/{doc}", headers=auth)
        self.assertEqual(self.client.get(f"/projects/{pid}", headers=auth).json()["documents"], [])


if __name__ == "__main__":
    unittest.main()
