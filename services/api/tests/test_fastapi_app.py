"""Tests for the FastAPI app surface (Phase D), via Starlette TestClient.

These run fully in-process (no live server, no DB) using the in-memory account
store and hashing embedder, so they verify routing, Pydantic validation, the
JWT auth dependency, RBAC, and OpenAPI generation deterministically.
"""

from __future__ import annotations

import os
import unittest

import _path  # noqa: F401

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
# Force the offline path (no DB, no LLM) for deterministic tests.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GEMINI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402


class FastApiAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())
        cls.client.__enter__()  # trigger lifespan startup

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def _register(self, email: str) -> str:
        r = self.client.post("/auth/register", json={"email": email, "password": "password123"})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["token"]

    def test_health_ok(self) -> None:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("status", r.json())

    def test_register_login_and_token_shape(self) -> None:
        r = self.client.post("/auth/register", json={"email": "fa-user@example.com", "password": "password123"})
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(len(body["token"].split(".")), 3)
        self.assertIn("admin", body["roles"])

        r2 = self.client.post("/auth/login", json={"email": "fa-user@example.com", "password": "password123"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["email"], "fa-user@example.com")

    def test_login_wrong_password_401(self) -> None:
        self.client.post("/auth/register", json={"email": "fa-bad@example.com", "password": "password123"})
        r = self.client.post("/auth/login", json={"email": "fa-bad@example.com", "password": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_refresh_and_logout_flow(self) -> None:
        reg = self.client.post(
            "/auth/register", json={"email": "fa-refresh@example.com", "password": "password123"}
        )
        self.assertEqual(reg.status_code, 201, reg.text)
        body = reg.json()
        refresh_token = body["refresh_token"]
        self.assertTrue(refresh_token)

        # Refresh rotates the token and returns a new valid access token.
        r = self.client.post("/auth/refresh", json={"refresh_token": refresh_token})
        self.assertEqual(r.status_code, 200, r.text)
        rotated = r.json()
        self.assertNotEqual(rotated["refresh_token"], refresh_token)
        self.assertEqual(len(rotated["token"].split(".")), 3)

        # Logout revokes the current (rotated) refresh token.
        r_logout = self.client.post("/auth/logout", json={"refresh_token": rotated["refresh_token"]})
        self.assertEqual(r_logout.status_code, 200)
        self.assertTrue(r_logout.json()["revoked"])

        # After logout the token no longer refreshes.
        r_after = self.client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
        self.assertEqual(r_after.status_code, 401)

        # The original (now-rotated) token is also rejected — reuse detection.
        r_reuse = self.client.post("/auth/refresh", json={"refresh_token": refresh_token})
        self.assertEqual(r_reuse.status_code, 401)

    def test_register_validation_error_422(self) -> None:
        # Password too short violates the Pydantic min_length=8 constraint.
        r = self.client.post("/auth/register", json={"email": "x@example.com", "password": "short"})
        self.assertEqual(r.status_code, 422)

    def test_query_requires_auth(self) -> None:
        r = self.client.post("/query", json={"question": "hello"})
        self.assertEqual(r.status_code, 401)

    def test_query_with_token_returns_200(self) -> None:
        token = self._register("fa-query@example.com")
        r = self.client.post(
            "/query", json={"question": "What is in the documents?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("status", body)
        self.assertIn("answer_markdown", body)

    def test_workspaces_with_token(self) -> None:
        token = self._register("fa-ws@example.com")
        r = self.client.get("/workspaces", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("organization", r.json())

    def test_openapi_schema_exposes_endpoints(self) -> None:
        r = self.client.get("/openapi.json")
        self.assertEqual(r.status_code, 200)
        paths = r.json()["paths"]
        for p in ("/health", "/auth/register", "/auth/login", "/query", "/query/stream",
                  "/workspaces", "/members", "/plans", "/billing", "/billing/change-plan",
                  "/notifications", "/ingest/upload", "/ingest/local", "/documents",
                  "/projects", "/archives", "/billing/webhook"):
            self.assertIn(p, paths)

    def test_documents_empty_for_new_tenant(self) -> None:
        token = self._register("fa-docs@example.com")
        r = self.client.get("/documents", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["documents"], [])

    def test_ingest_local_enqueues_and_status(self) -> None:
        import tempfile, pathlib, uuid as _uuid
        token = self._register("fa-ingest@example.com")
        # Create a tiny text file the pipeline will reject (not pdf/audio) — we
        # only assert the enqueue + job-status contract, not extraction success.
        p = pathlib.Path(tempfile.gettempdir()) / f"fa-{_uuid.uuid4().hex}.txt"
        p.write_text("hello world", encoding="utf-8")
        r = self.client.post(
            "/ingest/local",
            json={"document_id": str(_uuid.uuid4()), "file_path": str(p)},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 202, r.text)
        job_id = r.json()["job_id"]
        s = self.client.get(f"/ingest/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(s.status_code, 200)
        self.assertIn(s.json()["status"], ("uploaded", "processing", "ready", "failed"))

    def test_stream_returns_sse(self) -> None:
        token = self._register("fa-stream@example.com")
        r = self.client.post(
            "/query/stream", json={"question": "anything"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/event-stream", r.headers.get("content-type", ""))
        self.assertIn("event: done", r.text)


if __name__ == "__main__":
    unittest.main()
