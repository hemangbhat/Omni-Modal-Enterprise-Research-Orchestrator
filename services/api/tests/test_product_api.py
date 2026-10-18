"""HTTP tests for M3 part B: library query, archive, dashboard, search, invoices,
admin failures, webhook verification and readiness."""

from __future__ import annotations

import base64
import os
import tempfile
import time
import unittest
from unittest import mock

import _path  # noqa: F401
from _pdf import make_pdf

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
for _key in ("DATABASE_URL", "GEMINI_API_KEY", "GROQ_API_KEY", "S3_BUCKET", "STRIPE_SECRET_KEY"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.api.app import state as app_state  # noqa: E402
from omni_modal.security.auth import _make_jwt, jwt_secret_from_env  # noqa: E402


class ProductApiTests(unittest.TestCase):
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

    def _user(self, email: str) -> tuple[dict, dict]:
        reg = self.client.post("/auth/register", json={"email": email, "password": "password123"}).json()
        return {"Authorization": f"Bearer {reg['token']}"}, reg

    def _as(self, reg: dict, user_id: str, roles: list[str]) -> dict:
        token = _make_jwt(reg["tenant_id"], user_id, roles, int(time.time()) + 600, jwt_secret_from_env())
        return {"Authorization": f"Bearer {token}"}

    def _upload(self, auth: dict, text: str, name: str, data: bytes | None = None) -> str:
        r = self.client.post("/ingest/upload", headers=auth, json={
            "filename": name, "title": name.rsplit(".", 1)[0],
            "content_base64": base64.b64encode(data if data is not None else make_pdf(text)).decode()})
        self.assertEqual(r.status_code, 202, r.text)
        app_state.dispatcher.wait_idle()
        return r.json()["document_id"]

    # ── Library ──────────────────────────────────────────────────────────
    def test_library_search_filter_sort_and_pages(self) -> None:
        auth, _ = self._user("lib@example.com")
        ids = {name: self._upload(auth, f"Text about {name} topic.", f"{name}.pdf")
               for name in ("alpha", "bravo", "charlie")}
        self._upload(auth, "", "broken.pdf", data=b"%PDF-1.4 not really a pdf")

        page = self.client.get("/documents?sort=title&page_size=2", headers=auth).json()
        self.assertEqual(page["total"], 4)
        self.assertEqual([d["title"] for d in page["documents"]], ["alpha", "bravo"])
        second = self.client.get("/documents?sort=title&page_size=2&page=2", headers=auth).json()
        self.assertEqual([d["title"] for d in second["documents"]], ["broken", "charlie"])

        self.assertEqual([d["document_id"] for d in
                          self.client.get("/documents?q=BRAV", headers=auth).json()["documents"]], [ids["bravo"]])
        failed = self.client.get("/documents?status=failed", headers=auth).json()["documents"]
        self.assertEqual([d["title"] for d in failed], ["broken"])
        self.assertEqual(self.client.get("/documents?q=100%25", headers=auth).json()["total"], 0)
        self.assertEqual(self.client.get("/documents?sort=random", headers=auth).status_code, 400)

        project = self.client.post("/projects", json={"name": "P"}, headers=auth).json()["id"]
        self.client.post(f"/projects/{project}/documents", json={"document_id": ids["charlie"]}, headers=auth)
        in_project = self.client.get(f"/documents?project_id={project}", headers=auth).json()["documents"]
        self.assertEqual([d["title"] for d in in_project], ["charlie"])
        self.assertEqual(in_project[0]["projects"], [{"id": project, "name": "P"}])

    def test_archived_documents_leave_research_and_the_library_until_restored(self) -> None:
        auth, _ = self._user("archive@example.com")
        doc = self._upload(auth, "Quokka migration patterns were observed in spring.", "quokka.pdf")
        ask = lambda: self.client.post("/query", json={"question": "quokka migration"}, headers=auth).json()  # noqa: E731
        self.assertTrue(ask()["citations"])

        self.assertEqual(self.client.post(f"/documents/{doc}/archive", headers=auth).status_code, 200)
        self.assertEqual(ask()["citations"], [])
        self.assertEqual(self.client.get("/documents", headers=auth).json()["documents"], [])
        self.assertEqual([d["document_id"] for d in
                          self.client.get("/archives", headers=auth).json()["documents"]], [doc])

        self.client.post(f"/documents/{doc}/restore", headers=auth)
        self.assertTrue(ask()["citations"])
        self.assertEqual(self.client.get("/archives", headers=auth).json()["documents"], [])

    def test_only_uploader_or_admin_archives(self) -> None:
        auth, reg = self._user("archive-perm@example.com")
        doc = self._upload(auth, "Archive permissions document.", "perm.pdf")
        teammate = self._as(reg, "teammate", ["researcher"])
        self.assertEqual(self.client.post(f"/documents/{doc}/archive", headers=teammate).status_code, 403)
        other, _ = self._user("archive-other@example.com")
        self.assertEqual(self.client.post(f"/documents/{doc}/archive", headers=other).status_code, 404)

    # ── Dashboard and search ─────────────────────────────────────────────
    def test_dashboard_counts_come_from_records(self) -> None:
        auth, _ = self._user("dash@example.com")
        empty = self.client.get("/dashboard", headers=auth).json()
        self.assertEqual(empty["documents"]["total"], 0)
        self.assertEqual(empty["projects"], 0)
        self._upload(auth, "Dashboard counting document.", "count.pdf")
        self.client.post("/projects", json={"name": "Counted"}, headers=auth)
        board = self.client.get("/dashboard", headers=auth).json()
        self.assertEqual(board["documents"]["ready"], 1)
        self.assertEqual(board["projects"], 1)
        self.assertEqual(board["recent_documents"][0]["title"], "count")
        self.assertEqual(board["recent_activity"][0]["project_name"], "Counted")
        self.assertIn("metrics", board["usage"])

    def test_global_search_states_its_scope_and_stays_in_tenant(self) -> None:
        auth, _ = self._user("search@example.com")
        self._upload(auth, "Pelican budget.", "pelican-budget.pdf")
        self.client.post("/projects", json={"name": "Pelican launch"}, headers=auth)
        other, _ = self._user("search-other@example.com")
        self._upload(other, "Pelican secret.", "pelican-secret.pdf")

        hits = self.client.get("/search?q=pelican", headers=auth).json()
        self.assertEqual([d["title"] for d in hits["documents"]], ["pelican-budget"])
        self.assertEqual([p["name"] for p in hits["projects"]], ["Pelican launch"])
        self.assertIn("Document titles", hits["scope"])
        self.assertEqual(self.client.get("/search?q=p", headers=auth).json()["documents"], [])

    # ── Billing, admin, webhooks, readiness ──────────────────────────────
    def test_invoices_are_not_available_without_stripe(self) -> None:
        auth, _ = self._user("invoices@example.com")
        self.assertEqual(self.client.get("/billing/invoices", headers=auth).json(),
                         {"available": False, "invoices": []})

    def test_admin_sees_failed_jobs_and_retries(self) -> None:
        auth, reg = self._user("failures@example.com")
        doc = self._upload(auth, "", "bad.pdf", data=b"%PDF-1.4 broken")
        failures = self.client.get("/admin/ingestion/failures", headers=auth).json()["failures"]
        self.assertEqual([f["document"]["document_id"] for f in failures], [doc])
        self.assertTrue(failures[0]["job"]["error_message"])
        self.assertEqual(self.client.post(f"/admin/ingestion/failures/{doc}/retry", headers=auth).status_code, 202)
        researcher = self._as(reg, "someone", ["researcher"])
        self.assertEqual(self.client.get("/admin/ingestion/failures", headers=researcher).status_code, 403)

    def test_unverified_stripe_webhooks_are_refused(self) -> None:
        from omni_modal.saas.adapters import billing

        if not getattr(billing, "_STRIPE_AVAILABLE", False):
            self.skipTest("stripe library not installed")
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x"}, clear=False):
            os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
            adapter = billing.StripeBillingAdapter()
            with self.assertRaises(ValueError):
                adapter.verify_webhook(b'{"type": "checkout.session.completed"}', "")
            with mock.patch.dict(os.environ, {"OMERO_ALLOW_UNVERIFIED_WEBHOOKS": "true"}):
                self.assertEqual(adapter.verify_webhook(b'{"type": "x"}', "")["type"], "x")

    def test_readiness_reports_storage_and_model_provider(self) -> None:
        body = self.client.get("/health/ready").json()
        self.assertEqual(body["checks"]["storage"], "ok (local)")
        self.assertIn("not configured", body["checks"]["model_provider"])
        self.assertEqual(body["status"], "ready")


if __name__ == "__main__":
    unittest.main()
