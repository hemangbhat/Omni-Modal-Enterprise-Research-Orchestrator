"""Tests for the FastAPI app surface (Phase D), via Starlette TestClient.

These run fully in-process (no live server, no DB) using the in-memory account
store and hashing embedder, so they verify routing, Pydantic validation, the
JWT auth dependency, RBAC, and OpenAPI generation deterministically.
"""

from __future__ import annotations

import os
import re
import time
import unittest
from unittest import mock

import _path  # noqa: F401

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
# Force the offline path (no DB, no LLM) for deterministic tests.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GEMINI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.api.app import state as app_state  # noqa: E402
from omni_modal.qa.in_memory_store import StoredChunk  # noqa: E402
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
        auth = {"Authorization": f"Bearer {token}"}
        # Off by default: it lets the caller name any file on the server.
        self.assertEqual(self.client.post("/ingest/local", json={"file_path": str(p)}, headers=auth).status_code, 404)
        p.write_bytes(b"%PDF-1.4 local ingest " + _uuid.uuid4().hex.encode())
        p = p.rename(p.with_suffix(".pdf"))
        with mock.patch.dict(os.environ, {"OMERO_ALLOW_LOCAL_INGEST": "true"}):
            r = self.client.post("/ingest/local", json={"file_path": str(p)}, headers=auth)
        self.assertEqual(r.status_code, 202, r.text)
        job_id = r.json()["job_id"]
        s = self.client.get(f"/ingest/jobs/{job_id}", headers=auth)
        self.assertEqual(s.status_code, 200)
        self.assertIn(s.json()["status"], ("uploaded", "processing", "ready", "failed"))
        # Another tenant cannot read the job.
        other = {"Authorization": f"Bearer {self._register('fa-ingest-other@example.com')}"}
        self.assertEqual(self.client.get(f"/ingest/jobs/{job_id}", headers=other).status_code, 404)

    def test_stream_returns_sse(self) -> None:
        token = self._register("fa-stream@example.com")
        r = self.client.post(
            "/query/stream", json={"question": "anything"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/event-stream", r.headers.get("content-type", ""))
        self.assertIn("event: done", r.text)

    # ── Document-derived views carry no invented fields ──────────────────
    def _seed_document(self, email: str) -> dict:
        """Register a user and index one chunk for their tenant. Returns auth headers."""
        reg = self.client.post("/auth/register", json={"email": email, "password": "password123"}).json()
        app_state.vector_store.add(StoredChunk(
            tenant_id=reg["tenant_id"], document_id="doc-1", title="Q3 Board Minutes",
            source_type="pdf", chunk_id="doc-1-0", chunk_index=0,
            content="Revenue grew.", embedding=[0.0],
        ))
        return {"Authorization": f"Bearer {reg['token']}"}

    def test_projects_are_real_records_not_documents(self) -> None:
        auth = self._seed_document("fa-projects@example.com")
        self.assertEqual(self.client.get("/projects", headers=auth).json()["projects"], [])
        created = self.client.post("/projects", json={"name": "Q3 review"}, headers=auth)
        self.assertEqual(created.status_code, 201, created.text)
        items = self.client.get("/projects", headers=auth).json()["projects"]
        self.assertEqual([p["name"] for p in items], ["Q3 review"])
        self.assertEqual(items[0]["counts"]["documents"], 0)

    def test_archives_list_only_archived_documents(self) -> None:
        auth = self._seed_document("fa-archives@example.com")
        # A document is not "archived" just because it exists.
        self.assertEqual(self.client.get("/archives", headers=auth).json()["documents"], [])

    # ── MFA (TOTP step-up) ───────────────────────────────────────────────
    def _enrol_mfa(self, email: str) -> str:
        """Register, enrol and confirm MFA. Returns the TOTP secret.

        Confirms with the previous time step's code so the current and later
        steps stay unused for the test body (accepted codes are single-use).
        """
        token = self._register(email)
        auth = {"Authorization": f"Bearer {token}"}
        r = self._start_enrolment(token, email)
        self.assertEqual(r.status_code, 200, r.text)
        secret = r.json()["secret"]
        r = self.client.post(
            "/auth/mfa/confirm", headers=auth,
            json={"code": totp(secret, for_time=time.time() - 30)},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"enrolled": True})
        return secret

    def _start_enrolment(self, token: str, email: str, password: str = "password123"):
        return self.client.post(
            "/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"},
            json={"email": email, "password": password},
        )

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
        self.assertEqual(self._start_enrolment(token, "fa-mfa-pending@example.com").status_code, 200)
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

    def test_mfa_enrol_labels_the_authenticator_entry_with_the_account_email(self) -> None:
        token = self._register("fa-mfa-label@example.com")
        r = self._start_enrolment(token, "fa-mfa-label@example.com")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("OMERO%3Afa-mfa-label%40example.com", r.json()["otpauth_uri"])

    # ── Review fixes: enrolment is guarded, wrong codes lock, codes are single-use ──
    def test_mfa_enrol_requires_the_current_password(self) -> None:
        token = self._register("fa-mfa-pw@example.com")
        wrong = self._start_enrolment(token, "fa-mfa-pw@example.com", password="not-my-password")
        self.assertEqual(wrong.status_code, 403)
        missing = self.client.post("/auth/mfa/enroll", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(missing.status_code, 422)

    def test_mfa_enrol_rejects_another_accounts_credentials(self) -> None:
        self._register("fa-mfa-other@example.com")
        token = self._register("fa-mfa-mine@example.com")
        r = self._start_enrolment(token, "fa-mfa-other@example.com")
        self.assertEqual(r.status_code, 403)
        auth = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/auth/mfa/status", headers=auth).json(), {"enrolled": False})

    def test_mfa_enrol_cannot_replace_an_active_second_factor(self) -> None:
        secret = self._enrol_mfa("fa-mfa-keep@example.com")
        challenge = self._login("fa-mfa-keep@example.com").json()
        session = self.client.post(
            "/auth/mfa/login", json={"mfa_token": challenge["mfa_token"], "code": totp(secret)}
        ).json()
        again = self._start_enrolment(session["token"], "fa-mfa-keep@example.com")
        self.assertEqual(again.status_code, 409)
        auth = {"Authorization": f"Bearer {session['token']}"}
        self.assertEqual(self.client.get("/auth/mfa/status", headers=auth).json(), {"enrolled": True})
        # Password alone still does not sign in, and the original authenticator still works.
        relogin = self._login("fa-mfa-keep@example.com").json()
        self.assertIs(relogin.get("mfa_required"), True)
        ok = self.client.post(
            "/auth/mfa/login",
            json={"mfa_token": relogin["mfa_token"], "code": totp(secret, for_time=time.time() + 30)},
        )
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_mfa_code_cannot_be_reused_on_a_second_login(self) -> None:
        secret = self._enrol_mfa("fa-mfa-replay@example.com")
        code = totp(secret)
        first = self._login("fa-mfa-replay@example.com").json()
        ok = self.client.post("/auth/mfa/login", json={"mfa_token": first["mfa_token"], "code": code})
        self.assertEqual(ok.status_code, 200, ok.text)
        second = self._login("fa-mfa-replay@example.com").json()
        replay = self.client.post("/auth/mfa/login", json={"mfa_token": second["mfa_token"], "code": code})
        self.assertEqual(replay.status_code, 401)

    def test_mfa_wrong_codes_across_logins_lock_the_second_factor(self) -> None:
        secret = self._enrol_mfa("fa-mfa-spray@example.com")
        wrong = 0
        while wrong < 5:
            # A fresh login each time, so the per-challenge limit never applies.
            mfa_token = self._login("fa-mfa-spray@example.com").json()["mfa_token"]
            r = self.client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": "000000"})
            self.assertEqual(r.status_code, 401)
            wrong += 1
        mfa_token = self._login("fa-mfa-spray@example.com").json()["mfa_token"]
        locked = self.client.post(
            "/auth/mfa/login", json={"mfa_token": mfa_token, "code": totp(secret)}
        )
        self.assertEqual(locked.status_code, 429, locked.text)
        self.assertIsNone(locked.json()["detail"]["mfa_token"])
        self.assertIn("Too many wrong codes", locked.json()["detail"]["error"])
        self.assertGreater(int(locked.headers["Retry-After"]), 0)

    def test_mfa_wrong_codes_do_not_extend_the_challenge_lifetime(self) -> None:
        secret = self._enrol_mfa("fa-mfa-ttl@example.com")
        start = time.time()
        mfa_token = self._login("fa-mfa-ttl@example.com").json()["mfa_token"]
        with mock.patch("time.time", return_value=start + 200):
            bad = self.client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": "000000"})
            self.assertEqual(bad.status_code, 401)
            mfa_token = bad.json()["detail"]["mfa_token"]
        # 310 s after the password step: past the 5-minute challenge window,
        # even though a retry token was issued at 200 s.
        with mock.patch("time.time", return_value=start + 310):
            late = self.client.post(
                "/auth/mfa/login",
                json={"mfa_token": mfa_token, "code": totp(secret, for_time=start + 310)},
            )
        self.assertEqual(late.status_code, 401)

    def test_mfa_malformed_code_is_rejected_without_a_server_error(self) -> None:
        self._enrol_mfa("fa-mfa-unicode@example.com")
        mfa_token = self._login("fa-mfa-unicode@example.com").json()["mfa_token"]
        r = self.client.post(
            "/auth/mfa/login", json={"mfa_token": mfa_token, "code": "\uff11\uff12\uff13\uff14\uff15\uff16"}
        )
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.json()["detail"]["mfa_token"])  # the challenge survives a typo

    # ── Password reset ───────────────────────────────────────────────────
    def _sent_emails(self) -> list:
        return app_state.saas.email.sent

    def _request_reset(self, email: str) -> str:
        """Ask for a reset link and return the token from the email sent."""
        before = len(self._sent_emails())
        r = self.client.post("/auth/password/forgot", json={"email": email})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"sent": True})
        self.assertEqual(len(self._sent_emails()), before + 1)
        message = self._sent_emails()[-1]
        self.assertEqual(message.to, email)
        self.assertEqual(message.subject, "Reset your OMERO password")
        match = re.search(r"/reset-password\?token=([A-Za-z0-9_\-]+)", message.body)
        self.assertIsNotNone(match, message.body)
        return match.group(1)

    def test_forgot_unknown_email_returns_sent_and_sends_nothing(self) -> None:
        before = len(self._sent_emails())
        r = self.client.post("/auth/password/forgot", json={"email": "nobody-here@example.com"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"sent": True})
        self.assertEqual(len(self._sent_emails()), before)

    def test_forgot_then_reset_changes_password(self) -> None:
        self._register("fa-reset@example.com")
        token = self._request_reset("fa-reset@example.com")
        self.assertIn("expires in 30 minutes", self._sent_emails()[-1].body)
        r = self.client.post("/auth/password/reset", json={"token": token, "password": "brand-new-pass"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"reset": True})
        self.assertEqual(self._login("fa-reset@example.com").status_code, 401)
        ok = self.client.post(
            "/auth/login", json={"email": "fa-reset@example.com", "password": "brand-new-pass"}
        )
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_reset_link_cannot_be_reused(self) -> None:
        self._register("fa-reset-once@example.com")
        token = self._request_reset("fa-reset-once@example.com")
        first = self.client.post("/auth/password/reset", json={"token": token, "password": "first-new-pass"})
        self.assertEqual(first.status_code, 200, first.text)
        again = self.client.post("/auth/password/reset", json={"token": token, "password": "second-new-pass"})
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()["detail"], "This reset link is invalid or has expired.")
        still = self.client.post(
            "/auth/login", json={"email": "fa-reset-once@example.com", "password": "first-new-pass"}
        )
        self.assertEqual(still.status_code, 200)

    def test_reset_with_unknown_token_is_refused(self) -> None:
        r = self.client.post("/auth/password/reset", json={"token": "made-up", "password": "whatever-pass"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "This reset link is invalid or has expired.")

    def test_reset_revokes_existing_refresh_tokens(self) -> None:
        reg = self.client.post(
            "/auth/register", json={"email": "fa-reset-revoke@example.com", "password": "password123"}
        ).json()
        token = self._request_reset("fa-reset-revoke@example.com")
        self.client.post("/auth/password/reset", json={"token": token, "password": "brand-new-pass"})
        r = self.client.post("/auth/refresh", json={"refresh_token": reg["refresh_token"]})
        self.assertEqual(r.status_code, 401)

    def test_reset_rejects_short_password_without_spending_the_link(self) -> None:
        self._register("fa-reset-short@example.com")
        token = self._request_reset("fa-reset-short@example.com")
        short = self.client.post("/auth/password/reset", json={"token": token, "password": "short"})
        self.assertEqual(short.status_code, 400)
        self.assertEqual(short.json()["detail"], "Password must be at least 8 characters.")
        ok = self.client.post("/auth/password/reset", json={"token": token, "password": "long-enough-pass"})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_forgot_is_capped_per_address(self) -> None:
        self._register("fa-reset-cap@example.com")
        for _ in range(3):
            self._request_reset("fa-reset-cap@example.com")
        before = len(self._sent_emails())
        r = self.client.post("/auth/password/forgot", json={"email": "fa-reset-cap@example.com"})
        self.assertEqual(r.json(), {"sent": True})
        self.assertEqual(len(self._sent_emails()), before)

    def test_reset_keeps_mfa_in_force(self) -> None:
        self._enrol_mfa("fa-reset-mfa@example.com")
        token = self._request_reset("fa-reset-mfa@example.com")
        self.client.post("/auth/password/reset", json={"token": token, "password": "brand-new-pass"})
        body = self.client.post(
            "/auth/login", json={"email": "fa-reset-mfa@example.com", "password": "brand-new-pass"}
        ).json()
        self.assertIs(body.get("mfa_required"), True)

    def test_mfa_endpoints_require_bearer(self) -> None:
        self.assertEqual(self.client.post("/auth/mfa/enroll").status_code, 401)
        self.assertEqual(self.client.post("/auth/mfa/confirm", json={"code": "123456"}).status_code, 401)
        self.assertEqual(self.client.get("/auth/mfa/status").status_code, 401)
        self.assertEqual(self.client.post("/auth/mfa/disable", json={"code": "123456"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
