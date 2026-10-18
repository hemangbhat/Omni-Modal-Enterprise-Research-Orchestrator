"""State created before a backend restart is there after it (Postgres only).

Runs when ``OMERO_TEST_DATABASE_URL`` points at a disposable local database
(see ``_pg.py``). Each phase is a separate Python process, so nothing can
survive in memory between them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import _path  # noqa: F401
from _pg import migrate, disposable_pg_url

HERE = Path(__file__).resolve().parent


@unittest.skipUnless(disposable_pg_url(), "set OMERO_TEST_DATABASE_URL to a disposable Postgres")
class PostgresRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.url = disposable_pg_url()
        migrate(cls.url)
        cls._dir = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._dir.cleanup()

    def _phase(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "DATABASE_URL": self.url,
            "JWT_SECRET": "restart-test-secret",
            "LOCAL_STORAGE_DIR": self._dir.name,
            "EMBEDDING_DIMENSIONS": "384",
            "OMERO_DISABLE_DOTENV": "1",
        }
        for key in ("REDIS_URL", "S3_BUCKET", "GROQ_API_KEY", "GEMINI_API_KEY", "STRIPE_SECRET_KEY"):
            env.pop(key, None)
        return subprocess.run([sys.executable, str(HERE / "_restart_phase.py"), *args],
                              cwd=HERE, env=env, capture_output=True, text=True, timeout=300)

    def test_everything_survives_a_restart(self) -> None:
        created = self._phase("create", f"restart-{uuid.uuid4().hex[:8]}@example.com")
        self.assertEqual(created.returncode, 0, created.stderr[-2000:])
        line = next(l for l in created.stdout.splitlines() if l.startswith("RESULT "))
        ids = json.loads(line[len("RESULT "):])
        verified = self._phase("verify", json.dumps(ids))
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
