"""Ordinary requests get a generous limit; research, the assistant and uploads keep the strict one."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import _path  # noqa: F401

os.environ.setdefault("JWT_SECRET", "test-secret-for-fastapi")
for _key in ("DATABASE_URL", "REDIS_URL", "GROQ_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

from omni_modal.api.app import create_app  # noqa: E402
from omni_modal.security.rate_limiting import general_limits, heavy_limits  # noqa: E402


class RateLimitTierTests(unittest.TestCase):
    def test_defaults_and_overrides(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in ("RATE_LIMIT_USER_RPM", "RATE_LIMIT_HEAVY_USER_RPM"):
                os.environ.pop(key, None)
            self.assertEqual((general_limits().user_rpm, heavy_limits().user_rpm), (300, 20))
        with mock.patch.dict(os.environ, {"RATE_LIMIT_HEAVY_USER_RPM": "5", "RATE_LIMIT_USER_RPM": "nope"}):
            self.assertEqual((general_limits().user_rpm, heavy_limits().user_rpm), (300, 5))

    def test_browsing_is_not_blocked_but_questions_are_limited(self) -> None:
        with mock.patch.dict(os.environ, {"RATE_LIMIT_HEAVY_USER_RPM": "3"}), TestClient(create_app()) as client:
            token = client.post("/auth/register", json={"email": "tiers@example.com", "password": "password123"}).json()["token"]
            auth = {"Authorization": f"Bearer {token}"}
            for _ in range(40):  # well past the old 20-per-minute limit
                self.assertEqual(client.get("/documents", headers=auth).status_code, 200)
            codes = [client.post("/query", headers=auth, json={"question": "anything?"}).status_code for _ in range(4)]
            self.assertEqual(codes, [200, 200, 200, 429])
            self.assertEqual(client.get("/projects", headers=auth).status_code, 200)


if __name__ == "__main__":
    unittest.main()
