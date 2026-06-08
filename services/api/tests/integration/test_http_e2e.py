"""HTTP integration test — exercises the full stack end-to-end.

Starts the HTTP server on a random port, sends real HTTP requests, and
verifies the responses. No mocks. Uses the in-memory path (no DATABASE_URL).

Run:
    python -m unittest services/api/tests/integration/test_http_e2e.py
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import HTTPServer

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))


JWT_SECRET = "integration-test-secret-xyzabc123"


def _make_jwt(tenant: str, user: str, roles: list[str]) -> str:
    def b64e(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64e(json.dumps({
        "tenant_id": tenant,
        "user_id": user,
        "roles": roles,
        "exp": int(time.time()) + 3600,
    }).encode())
    sig_bytes = hmac.new(
        JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{payload}.{b64e(sig_bytes)}"


def _get(url: str, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def _post(url: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Length", str(len(data)))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class HttpE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["JWT_SECRET"] = JWT_SECRET
        os.environ.pop("DATABASE_URL", None)  # force in-memory path
        os.environ["EMBEDDING_BACKEND"] = "hashing"
        os.environ["EMBEDDING_DIMENSIONS"] = "64"  # tiny vectors for fast tests

        # Import AFTER env vars are set so class-level init picks them up
        from omni_modal.main import OmniModalHandler

        server = HTTPServer(("127.0.0.1", 0), OmniModalHandler)
        cls._port = server.server_address[1]
        cls._server = server
        OmniModalHandler.queue.start_worker()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.5)  # brief wait for server to be ready
        cls._token = _make_jwt("t1", "u1", ["researcher", "admin"])
        cls._base = f"http://127.0.0.1:{cls._port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.shutdown()

    def test_health_returns_ok(self) -> None:
        status, body = _get(f"{self._base}/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("components", body)

    def test_unauthenticated_query_returns_401(self) -> None:
        status, body = _post(f"{self._base}/query/stream", {"question": "test"})
        self.assertEqual(status, 401)

    def test_query_returns_answer(self) -> None:
        # Ensure we use the same secret as the running server
        os.environ["JWT_SECRET"] = JWT_SECRET
        status, body = _post(
            f"{self._base}/query",
            {"question": "What is this system?"},
            token=self._token,
        )
        self.assertEqual(status, 200)
        self.assertIn("answer_markdown", body)
        self.assertIn("status", body)

    def test_documents_endpoint_returns_list(self) -> None:
        status, body = _get(f"{self._base}/documents", token=self._token)
        self.assertEqual(status, 200)
        self.assertIn("documents", body)
        self.assertIsInstance(body["documents"], list)

    def test_upload_and_poll_job(self) -> None:
        # Create a minimal valid PDF-like binary (just enough for the pipeline to accept)
        content = b"%PDF-1.4 hello world test document"
        b64 = base64.b64encode(content).decode()
        status, body = _post(
            f"{self._base}/ingest/upload",
            {"filename": "test.pdf", "content_base64": b64, "title": "E2E Test"},
            token=self._token,
        )
        self.assertEqual(status, 202)
        self.assertIn("job_id", body)

        job_id = body["job_id"]
        # Poll until done (max 10 seconds)
        final_status = None
        for _ in range(20):
            time.sleep(0.5)
            s, job = _get(f"{self._base}/ingest/jobs/{job_id}", token=self._token)
            self.assertEqual(s, 200)
            if job["status"] in ("ready", "failed"):
                final_status = job["status"]
                break
        # A minimal PDF may fail extraction gracefully — either outcome is valid
        self.assertIn(final_status, ("ready", "failed"))

    def test_options_preflight_returns_cors_headers(self) -> None:
        req = urllib.request.Request(f"{self._base}/query", method="OPTIONS")
        req.add_header("Origin", "http://localhost:3000")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 204)
            self.assertIn("Access-Control-Allow-Origin", dict(r.headers))


if __name__ == "__main__":
    unittest.main()
