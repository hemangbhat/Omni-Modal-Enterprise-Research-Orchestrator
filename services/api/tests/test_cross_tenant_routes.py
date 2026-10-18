"""Every route that takes an id refuses another tenant's id, and no list leaks across tenants.

Tenant A creates one of each resource. Tenant B (an admin of its own
workspace, so role checks pass) calls every route that has a path parameter
with A's ids. Each call must answer 403 or 404, never 2xx and never a
validation error that would hide the check, and A's data must be unchanged.

The coverage test fails when a new route with a path parameter is added and
not listed here, so new endpoints cannot skip the check.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest

import _path  # noqa: F401
from _pdf import make_pdf
from _pg import disposable_pg_url

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
for _key in ("DATABASE_URL", "GEMINI_API_KEY", "GROQ_API_KEY", "S3_BUCKET", "REDIS_URL"):
    os.environ.pop(_key, None)
# With OMERO_TEST_DATABASE_URL (CI's Postgres job) the same checks run on the Postgres stores.
if disposable_pg_url():
    os.environ["DATABASE_URL"] = disposable_pg_url()
    os.environ.setdefault("EMBEDDING_DIMENSIONS", "384")

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.api.app import state as app_state  # noqa: E402

MARKER = "Quokka7731"  # appears only in tenant A's data

# (method, path template, json body or None, query string)
CALLS: list[tuple[str, str, dict | None, str]] = [
    ("GET", "/research/sessions/{session_id}", None, ""),
    ("PATCH", "/research/sessions/{session_id}", {"title": "taken"}, ""),
    ("DELETE", "/research/sessions/{session_id}", None, ""),
    ("POST", "/research/sessions/{session_id}/report", None, ""),
    ("PUT", "/research/sessions/{session_id}/project", {"project_id": "{project_id}"}, ""),
    ("PUT", "/documents/{document_id}/content", None, ""),
    ("POST", "/documents/{document_id}/complete", None, ""),
    ("GET", "/documents/{document_id}", None, ""),
    ("DELETE", "/documents/{document_id}", None, ""),
    ("POST", "/documents/{document_id}/reprocess", None, ""),
    ("GET", "/documents/{document_id}/download", None, ""),
    ("GET", "/documents/{document_id}/content", None, ""),
    ("POST", "/documents/{document_id}/archive", None, ""),
    ("POST", "/documents/{document_id}/restore", None, ""),
    ("GET", "/ingest/jobs/{job_id}", None, ""),
    ("GET", "/entities/{document_id}", None, ""),
    ("POST", "/admin/ingestion/failures/{document_id}/retry", None, ""),
    ("GET", "/projects/{project_id}", None, ""),
    ("PATCH", "/projects/{project_id}", {"name": "taken"}, ""),
    ("POST", "/projects/{project_id}/archive", None, ""),
    ("POST", "/projects/{project_id}/restore", None, ""),
    ("POST", "/projects/{project_id}/documents", {"document_id": "{own_document_id}"}, ""),
    ("DELETE", "/projects/{project_id}/documents/{document_id}", None, ""),
    ("POST", "/projects/{project_id}/items", {"kind": "note", "title": "taken"}, ""),
    ("PATCH", "/projects/{project_id}/items/{item_id}", {"title": "taken"}, ""),
    ("DELETE", "/projects/{project_id}/items/{item_id}", None, ""),
    ("GET", "/projects/{project_id}/search", None, "?q=" + MARKER),
    ("POST", "/projects/{project_id}/reports", None, ""),
    ("GET", "/reports/{report_id}", None, ""),
    ("GET", "/reports/{report_id}/download", None, ""),
]

# Lists and searches B may call freely; none may contain A's marker.
LISTS = [
    "/documents", "/documents?q=" + MARKER, "/archives", "/projects", "/reports", "/research/sessions",
    "/dashboard", "/search?q=" + MARKER, "/notifications", "/admin/ingestion/failures",
]


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


class CrossTenantRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        os.environ["LOCAL_STORAGE_DIR"] = cls._dir.name
        cls.client = TestClient(create_app())
        cls.client.__enter__()
        app_state.rate_limiter = None  # this test makes ~50 calls in a row; limits are tested elsewhere
        import uuid  # noqa: PLC0415

        run = uuid.uuid4().hex[:8]  # a Postgres database outlives one run
        cls.a = cls._register(f"tenant-a-{run}@example.com")
        cls.b = cls._register(f"tenant-b-{run}@example.com")
        cls.ids = cls._seed_tenant_a()
        cls.ids["own_document_id"] = cls._upload(cls.b, "Tenant B's own document.", "b.pdf")[0]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        os.environ.pop("LOCAL_STORAGE_DIR", None)
        cls._dir.cleanup()

    @classmethod
    def _register(cls, email: str) -> dict:
        reg = cls.client.post("/auth/register", json={"email": email, "password": "password123"}).json()
        return {"Authorization": f"Bearer {reg['token']}"}

    @classmethod
    def _upload(cls, auth: dict, text: str, name: str) -> tuple[str, str | None]:
        r = cls.client.post("/ingest/upload", headers=auth, json={
            "filename": name, "content_base64": base64.b64encode(make_pdf(text)).decode(), "title": name})
        assert r.status_code == 202, r.text
        app_state.dispatcher.wait_idle()
        return r.json()["document_id"], r.json().get("job_id")

    @classmethod
    def _seed_tenant_a(cls) -> dict:
        a = cls.a
        doc, job = cls._upload(a, f"The {MARKER} programme budget is 2.4 million dollars.", f"{MARKER}.pdf")
        if job is None:
            job = app_state.documents.latest_job(cls._tenant(a), doc).id
        pid = cls.client.post("/projects", headers=a, json={"name": f"{MARKER} project"}).json()["id"]
        cls.client.post(f"/projects/{pid}/documents", headers=a, json={"document_id": doc})
        item = cls.client.post(f"/projects/{pid}/items", headers=a,
                               json={"kind": "note", "title": f"{MARKER} note", "body": MARKER}).json()["id"]
        sid = _events(cls.client.post("/query/stream", headers=a,
                                      json={"question": f"What is the {MARKER} budget?"}).text)[0][1]["session_id"]
        report = cls.client.post(f"/projects/{pid}/reports", headers=a).json()["id"]
        return {"document_id": doc, "job_id": job, "project_id": pid, "item_id": item,
                "session_id": sid, "report_id": report}

    @staticmethod
    def _tenant(auth: dict) -> str:
        from omni_modal.security.auth import jwt_secret_from_env, verify_jwt  # noqa: PLC0415

        return verify_jwt(auth["Authorization"].split()[1], jwt_secret_from_env()).tenant_id

    def _fill(self, value):
        if isinstance(value, str):
            for key, val in self.ids.items():
                value = value.replace("{" + key + "}", str(val))
            return value
        if isinstance(value, dict):
            return {k: self._fill(v) for k, v in value.items()}
        return value

    def test_every_id_route_is_covered(self) -> None:
        listed = {(m, p) for m, p, _b, _q in CALLS}
        routes = {
            (method, route.path)
            for route in self.client.app.routes
            if "{" in getattr(route, "path", "")
            for method in (getattr(route, "methods", None) or ())
            if method not in ("HEAD", "OPTIONS")
        }
        self.assertEqual(sorted(routes - listed), [], "New routes need a cross-tenant entry in CALLS")

    def test_other_tenant_ids_are_refused(self) -> None:
        for method, template, body, query in CALLS:
            with self.subTest(route=f"{method} {template}"):
                path = self._fill(template) + query
                kwargs = {"headers": self.b}
                if body is not None:
                    kwargs["json"] = self._fill(body)
                if method == "PUT" and template.endswith("/content"):
                    kwargs["content"] = b"%PDF-1.4 stolen"
                r = self.client.request(method, path, **kwargs)
                self.assertIn(r.status_code, (403, 404), f"{method} {path} -> {r.status_code} {r.text[:200]}")
                self.assertNotIn(MARKER, r.text)

    def test_lists_never_show_other_tenant_data(self) -> None:
        for path in LISTS:
            with self.subTest(path=path):
                r = self.client.get(path, headers=self.b)
                self.assertEqual(r.status_code, 200, f"{path} -> {r.status_code}")
                self.assertNotIn(MARKER, r.text)

    def test_research_never_retrieves_other_tenant_chunks(self) -> None:
        r = self.client.post("/query", headers=self.b, json={"question": f"What is the {MARKER} budget?"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(MARKER, r.text.replace(f"What is the {MARKER} budget?", ""))

    def test_tenant_a_data_is_untouched(self) -> None:
        self.test_other_tenant_ids_are_refused()
        a, ids = self.a, self.ids
        doc = self.client.get(f"/documents/{ids['document_id']}", headers=a)
        self.assertEqual(doc.status_code, 200)
        self.assertIsNone(doc.json()["document"].get("archived_at"))
        project = self.client.get(f"/projects/{ids['project_id']}", headers=a).json()
        self.assertEqual(project["project"]["name"], f"{MARKER} project")
        self.assertIsNone(project["project"].get("archived_at"))
        self.assertTrue(any(i["id"] == ids["item_id"] and i["title"] == f"{MARKER} note"
                            for i in project["items"]))
        self.assertEqual(self.client.get(f"/research/sessions/{ids['session_id']}", headers=a).status_code, 200)


if __name__ == "__main__":
    unittest.main()
