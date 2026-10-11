"""Tests for the FastAPI app surface (Phase D), via Starlette TestClient.

These run fully in-process (no live server, no DB) using the in-memory account
store and hashing embedder, so they verify routing, Pydantic validation, the
JWT auth dependency, RBAC, and OpenAPI generation deterministically.
"""

from __future__ import annotations

import os
import time
import unittest

import _path  # noqa: F401

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
# Force the offline path (no DB, no LLM) for deterministic tests.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GEMINI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.security.mfa import totp  # noqa: E402


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

    # ── MFA (TOTP step-up) ───────────────────────────────────────────────
    def _enrol_mfa(self, email: str) -> str:
        """Register, enrol and confirm MFA. Returns the TOTP secret.

        Confirms with the previous time step's code so the current and later
        steps stay unused for the test body (accepted codes are single-use).
        """
        token = self._register(email)
        auth = {"Authorization": f"Bearer {token}"}
        r = self.client.post("/auth/mfa/enroll", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        secret = r.json()["secret"]
        r = self.client.post(
            "/auth/mfa/confirm", headers=auth,
            json={"code": totp(secret, for_time=time.time() - 30)},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"enrolled": True})
        return secret

    def _login(self, email: str):
        return self.client.post("/auth/login", json={"email": email, "password": "password123"})

    def test_login_without_mfa_keeps_auth_response_shape(self) -> None:
        self._register("fa-nomfa@example.com")
        body = self._login("fa-nomfa@example.com").json()
        self.assertNotIn("mfa_required", body)
        for key in ("token", "refresh_token", "roles", "tenant_id", "user_id", "email", "expires_at"):
            self.assertIn(key, body)

    def test_started_but_unconfirmed_enrolment_does_not_gate_login(self) -> None:
        token = self._register("fa-mfa-pending@example.com")
        self.client.post("/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"})
        body = self._login("fa-mfa-pending@example.com").json()
        self.assertNotIn("mfa_required", body)
        self.assertIn("token", body)

    def test_mfa_enrol_confirm_then_login_requires_code(self) -> None:
        self._enrol_mfa("fa-mfa-gate@example.com")
        r = self._login("fa-mfa-gate@example.com")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIs(body["mfa_required"], True)
        self.assertTrue(body["mfa_token"])
        self.assertNotIn("token", body)
        self.assertNotIn("refresh_token", body)

    def test_mfa_login_with_valid_code_issues_tokens(self) -> None:
        secret = self._enrol_mfa("fa-mfa-ok@example.com")
        challenge = self._login("fa-mfa-ok@example.com").json()
        r = self.client.post(
            "/auth/mfa/login", json={"mfa_token": challenge["mfa_token"], "code": totp(secret)}
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["token"].split(".")), 3)
        self.assertEqual(body["email"], "fa-mfa-ok@example.com")
        self.assertTrue(body["refresh_token"])

    def test_mfa_token_is_single_use(self) -> None:
        secret = self._enrol_mfa("fa-mfa-once@example.com")
        challenge = self._login("fa-mfa-once@example.com").json()
        first = self.client.post(
            "/auth/mfa/login", json={"mfa_token": challenge["mfa_token"], "code": totp(secret)}
        )
        self.assertEqual(first.status_code, 200, first.text)
        again = self.client.post(
            "/auth/mfa/login",
            json={"mfa_token": challenge["mfa_token"], "code": totp(secret, for_time=time.time() + 30)},
        )
        self.assertEqual(again.status_code, 401)

    def test_mfa_wrong_code_returns_a_fresh_challenge_token(self) -> None:
        secret = self._enrol_mfa("fa-mfa-retry@example.com")
        challenge = self._login("fa-mfa-retry@example.com").json()
        bad = self.client.post(
            "/auth/mfa/login", json={"mfa_token": challenge["mfa_token"], "code": "000000"}
        )
        self.assertEqual(bad.status_code, 401)
        next_token = bad.json()["detail"]["mfa_token"]
        self.assertTrue(next_token)
        self.assertNotEqual(next_token, challenge["mfa_token"])
        ok = self.client.post("/auth/mfa/login", json={"mfa_token": next_token, "code": totp(secret)})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_mfa_five_wrong_codes_kills_challenge(self) -> None:
        secret = self._enrol_mfa("fa-mfa-lock@example.com")
        mfa_token = self._login("fa-mfa-lock@example.com").json()["mfa_token"]
        seen = [mfa_token]
        for attempt in range(1, 6):
            r = self.client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": "000000"})
            self.assertEqual(r.status_code, 401)
            mfa_token = r.json()["detail"]["mfa_token"]
            if attempt < 5:
                self.assertTrue(mfa_token, f"attempt {attempt} should allow a retry")
                seen.append(mfa_token)
        self.assertIsNone(mfa_token)
        for stale in seen:
            r = self.client.post("/auth/mfa/login", json={"mfa_token": stale, "code": totp(secret)})
            self.assertEqual(r.status_code, 401)

    def test_mfa_disable_restores_password_only_login(self) -> None:
        secret = self._enrol_mfa("fa-mfa-off@example.com")
        challenge = self._login("fa-mfa-off@example.com").json()
        session = self.client.post(
            "/auth/mfa/login", json={"mfa_token": challenge["mfa_token"], "code": totp(secret)}
        ).json()
        auth = {"Authorization": f"Bearer {session['token']}"}
        self.assertEqual(self.client.get("/auth/mfa/status", headers=auth).json(), {"enrolled": True})
        bad = self.client.post("/auth/mfa/disable", headers=auth, json={"code": "000000"})
        self.assertEqual(bad.status_code, 400)
        r = self.client.post(
            "/auth/mfa/disable", headers=auth,
            json={"code": totp(secret, for_time=time.time() + 30)},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"enrolled": False})
        self.assertIn("token", self._login("fa-mfa-off@example.com").json())

    def test_mfa_enrol_labels_the_authenticator_entry_with_the_given_name(self) -> None:
        token = self._register("fa-mfa-label@example.com")
        r = self.client.post(
            "/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"},
            json={"account_name": "fa-mfa-label@example.com"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("OMERO%3Afa-mfa-label%40example.com", r.json()["otpauth_uri"])

    def test_mfa_endpoints_require_bearer(self) -> None:
        self.assertEqual(self.client.post("/auth/mfa/enroll").status_code, 401)
        self.assertEqual(self.client.post("/auth/mfa/confirm", json={"code": "123456"}).status_code, 401)
        self.assertEqual(self.client.get("/auth/mfa/status").status_code, 401)
        self.assertEqual(self.client.post("/auth/mfa/disable", json={"code": "123456"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
