"""HTTP tests for durable uploads: slot → bytes → complete → ready, plus
duplicates, download, delete, reprocess, size limits and tenant isolation.

In-process (TestClient), no database, local file storage in a temp folder.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
import unittest

import _path  # noqa: F401
from _pdf import make_pdf

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
for _key in ("DATABASE_URL", "GEMINI_API_KEY", "GROQ_API_KEY", "S3_BUCKET"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.api.app import state as app_state  # noqa: E402


class DocumentsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._storage_dir = tempfile.TemporaryDirectory()
        cls._old_storage = os.environ.get("LOCAL_STORAGE_DIR")
        os.environ["LOCAL_STORAGE_DIR"] = cls._storage_dir.name
        cls.client = TestClient(create_app())
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)
        if cls._old_storage is None:
            os.environ.pop("LOCAL_STORAGE_DIR", None)
        else:
            os.environ["LOCAL_STORAGE_DIR"] = cls._old_storage
        cls._storage_dir.cleanup()

    def _user(self, email: str) -> dict:
        reg = self.client.post("/auth/register", json={"email": email, "password": "password123"}).json()
        return {"Authorization": f"Bearer {reg['token']}"}

    def _slot(self, auth: dict, data: bytes, name: str = "minutes.pdf"):
        return self.client.post("/documents/uploads", headers=auth, json={
            "filename": name, "size_bytes": len(data), "content_type": "application/pdf",
            "sha256": hashlib.sha256(data).hexdigest(),
        })

    def _upload(self, auth: dict, data: bytes, name: str = "minutes.pdf") -> dict:
        r = self._slot(auth, data, name)
        self.assertIn(r.status_code, (200, 201), r.text)
        body = r.json()
        if body["duplicate"]:
            return body
        self.assertTrue(body["upload"]["requires_auth"])
        put = self.client.put(body["upload"]["url"], content=data, headers=auth)
        self.assertEqual(put.status_code, 200, put.text)
        done = self.client.post(f"/documents/{body['document']['id']}/complete", headers=auth)
        self.assertEqual(done.status_code, 202, done.text)
        app_state.dispatcher.wait_idle()
        return body

    def test_full_flow_reaches_ready_and_is_searchable(self) -> None:
        auth = self._user("doc-flow@example.com")
        data = make_pdf("Northwind revenue grew 18 percent in Q3.", "Headcount rose to 145.")
        slot = self._upload(auth, data)
        doc_id = slot["document"]["id"]
        self.assertEqual(slot["job"]["stage"], "awaiting_upload")

        detail = self.client.get(f"/documents/{doc_id}", headers=auth).json()
        self.assertEqual(detail["document"]["status"], "ready")
        self.assertEqual(detail["job"]["stage"], "done")
        self.assertEqual(detail["document"]["size_bytes"], len(data))
        self.assertNotIn("storage_key", detail["document"])

        listed = self.client.get("/documents", headers=auth).json()["documents"]
        self.assertEqual([d["document_id"] for d in listed], [doc_id])

        answer = self.client.post("/query", json={"question": "How much did revenue grow?"}, headers=auth).json()
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["citations"][0]["page_number"], 1)

    def test_duplicate_upload_returns_existing_document_without_using_quota(self) -> None:
        auth = self._user("doc-dup@example.com")
        data = make_pdf("Duplicate check document.")
        first = self._upload(auth, data)
        used = self.client.get("/usage", headers=auth).json()["metrics"]["uploads"]["used"]
        again = self._slot(auth, data, name="copy.pdf")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(again.json()["document"]["id"], first["document"]["id"])
        self.assertEqual(self.client.get("/usage", headers=auth).json()["metrics"]["uploads"]["used"], used)

    def test_download_returns_the_original_bytes(self) -> None:
        auth = self._user("doc-download@example.com")
        data = make_pdf("Download me.")
        doc_id = self._upload(auth, data)["document"]["id"]
        link = self.client.get(f"/documents/{doc_id}/download", headers=auth).json()
        self.assertTrue(link["requires_auth"])
        file = self.client.get(link["url"], headers=auth)
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.content, data)
        self.assertIn('filename="minutes.pdf"', file.headers["content-disposition"])

    def test_delete_removes_document_and_its_search_results(self) -> None:
        auth = self._user("doc-delete@example.com")
        doc_id = self._upload(auth, make_pdf("Zebra migration budget approved."))["document"]["id"]
        self.assertEqual(self.client.delete(f"/documents/{doc_id}", headers=auth).status_code, 200)
        self.assertEqual(self.client.get(f"/documents/{doc_id}", headers=auth).status_code, 404)
        self.assertEqual(self.client.get("/documents", headers=auth).json()["documents"], [])
        answer = self.client.post("/query", json={"question": "zebra budget"}, headers=auth).json()
        self.assertEqual(answer["citations"], [])

    def test_reprocess_runs_a_new_job(self) -> None:
        auth = self._user("doc-reprocess@example.com")
        doc_id = self._upload(auth, make_pdf("Reprocess me."))["document"]["id"]
        first_job = self.client.get(f"/documents/{doc_id}", headers=auth).json()["job"]["id"]
        r = self.client.post(f"/documents/{doc_id}/reprocess", headers=auth)
        self.assertEqual(r.status_code, 202, r.text)
        app_state.dispatcher.wait_idle()
        detail = self.client.get(f"/documents/{doc_id}", headers=auth).json()
        self.assertNotEqual(detail["job"]["id"], first_job)
        self.assertEqual(detail["job"]["stage"], "done")

    def test_unreadable_file_fails_with_a_reason(self) -> None:
        auth = self._user("doc-bad@example.com")
        doc_id = self._upload(auth, b"%PDF-1.4 this is not really a pdf")["document"]["id"]
        detail = self.client.get(f"/documents/{doc_id}", headers=auth).json()
        self.assertEqual(detail["document"]["status"], "failed")
        self.assertTrue(detail["document"]["error_message"])

    def test_oversized_body_is_refused(self) -> None:
        auth = self._user("doc-big@example.com")
        data = make_pdf("small")
        slot = self._slot(auth, data).json()
        r = self.client.put(slot["upload"]["url"], content=data + b"x" * 5000, headers=auth)
        self.assertEqual(r.status_code, 413)

    def test_bad_requests_are_explained(self) -> None:
        auth = self._user("doc-badreq@example.com")
        r = self.client.post("/documents/uploads", headers=auth, json={
            "filename": "virus.exe", "size_bytes": 10, "sha256": "a" * 64})
        self.assertEqual(r.status_code, 400)
        self.assertIn("PDF and audio", r.json()["detail"])
        slot = self._slot(auth, make_pdf("never sent")).json()
        r = self.client.post(f"/documents/{slot['document']['id']}/complete", headers=auth)
        self.assertEqual(r.status_code, 409)

    def test_rejected_uploads_do_not_use_quota(self) -> None:
        auth = self._user("doc-quota@example.com")
        used = lambda: self.client.get("/usage", headers=auth).json()["metrics"]["uploads"]["used"]  # noqa: E731
        before = used()
        rejected = (
            {"filename": "x.exe", "size_bytes": 10, "sha256": "a" * 64},     # wrong type
            {"filename": "x.pdf", "size_bytes": 10, "sha256": "z" * 64},     # not a hex hash
            {"filename": "x.pdf", "size_bytes": 10**10, "sha256": "b" * 64},  # too large
        )
        for body in rejected:
            self.assertEqual(self.client.post("/documents/uploads", headers=auth, json=body).status_code, 400)
        self.assertEqual(used(), before)
        ok = self.client.post("/documents/uploads", headers=auth,
                              json={"filename": "x.pdf", "size_bytes": 10, "sha256": "c" * 64})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(used(), before + 1)

    def test_only_the_uploader_may_send_bytes_and_is_checked_first(self) -> None:
        owner_reg = self.client.post("/auth/register", json={"email": "doc-up@example.com",
                                                             "password": "password123"}).json()
        owner = {"Authorization": f"Bearer {owner_reg['token']}"}
        slot = self._slot(owner, make_pdf("Owner only.")).json()
        # Another user of the same tenant cannot send the bytes.
        from omni_modal.security.auth import _make_jwt, jwt_secret_from_env

        teammate = {"Authorization": "Bearer " + _make_jwt(
            owner_reg["tenant_id"], "someone-else", ["researcher"], int(time.time()) + 600, jwt_secret_from_env())}
        r = self.client.put(slot["upload"]["url"], content=b"x" * 10, headers=teammate)
        self.assertEqual(r.status_code, 403)

    def test_other_tenant_cannot_touch_the_document(self) -> None:
        owner = self._user("doc-owner@example.com")
        intruder = self._user("doc-intruder@example.com")
        doc_id = self._upload(owner, make_pdf("Private board minutes."))["document"]["id"]
        for method, path in (
            ("get", f"/documents/{doc_id}"),
            ("get", f"/documents/{doc_id}/download"),
            ("get", f"/documents/{doc_id}/content"),
            ("delete", f"/documents/{doc_id}"),
            ("post", f"/documents/{doc_id}/reprocess"),
            ("post", f"/documents/{doc_id}/complete"),
        ):
            r = getattr(self.client, method)(path, headers=intruder)
            self.assertEqual(r.status_code, 404, f"{method} {path}: {r.status_code}")
        r = self.client.put(f"/documents/{doc_id}/content", content=b"x", headers=intruder)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get("/documents", headers=intruder).json()["documents"], [])
        self.assertEqual(self.client.get(f"/documents/{doc_id}", headers=owner).status_code, 200)

    def test_base64_route_uses_the_same_durable_flow(self) -> None:
        import base64

        auth = self._user("doc-b64@example.com")
        data = make_pdf("Base64 upload.")
        body = {"filename": "b64.pdf", "content_base64": base64.b64encode(data).decode()}
        r = self.client.post("/ingest/upload", json=body, headers=auth)
        self.assertEqual(r.status_code, 202, r.text)
        app_state.dispatcher.wait_idle()
        job = self.client.get(f"/ingest/jobs/{r.json()['job_id']}", headers=auth).json()
        self.assertEqual(job["stage"], "done")
        self.assertTrue(self.client.post("/ingest/upload", json=body, headers=auth).json()["duplicate"])


if __name__ == "__main__":
    unittest.main()
