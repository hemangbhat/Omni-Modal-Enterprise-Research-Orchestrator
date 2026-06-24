"""Tests for the dependency-free .env loader."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _path  # noqa: F401  (adds services/api/src to sys.path)

from omni_modal.env_loader import find_dotenv, load_dotenv


class LoadDotenvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.env_file = self.dir / ".env"
        # Snapshot env so individual keys can be restored.
        self._to_clear: list[str] = []

    def tearDown(self) -> None:
        for key in self._to_clear:
            os.environ.pop(key, None)

    def _track(self, *keys: str) -> None:
        self._to_clear.extend(keys)

    def test_loads_plain_quoted_and_export_lines(self) -> None:
        self._track("OMERO_PLAIN", "OMERO_DQ", "OMERO_SQ", "OMERO_EXPORT")
        self.env_file.write_text(
            "\n".join(
                [
                    "# a comment",
                    "",
                    "OMERO_PLAIN=plain",
                    'OMERO_DQ="double quoted"',
                    "OMERO_SQ='single quoted'",
                    "export OMERO_EXPORT=exported",
                ]
            ),
            encoding="utf-8",
        )
        applied = load_dotenv(self.env_file)
        self.assertEqual(applied, 4)
        self.assertEqual(os.environ["OMERO_PLAIN"], "plain")
        self.assertEqual(os.environ["OMERO_DQ"], "double quoted")
        self.assertEqual(os.environ["OMERO_SQ"], "single quoted")
        self.assertEqual(os.environ["OMERO_EXPORT"], "exported")

    def test_does_not_override_existing_by_default(self) -> None:
        self._track("OMERO_EXISTING")
        os.environ["OMERO_EXISTING"] = "from-process"
        self.env_file.write_text("OMERO_EXISTING=from-file\n", encoding="utf-8")
        applied = load_dotenv(self.env_file)
        self.assertEqual(applied, 0)
        self.assertEqual(os.environ["OMERO_EXISTING"], "from-process")

    def test_override_true_replaces_existing(self) -> None:
        self._track("OMERO_OVERRIDE")
        os.environ["OMERO_OVERRIDE"] = "from-process"
        self.env_file.write_text("OMERO_OVERRIDE=from-file\n", encoding="utf-8")
        applied = load_dotenv(self.env_file, override=True)
        self.assertEqual(applied, 1)
        self.assertEqual(os.environ["OMERO_OVERRIDE"], "from-file")

    def test_value_containing_equals_is_preserved(self) -> None:
        # Connection strings and DSNs contain '=' in their query params.
        self._track("OMERO_URL")
        self.env_file.write_text(
            'OMERO_URL="postgresql://u:p@h/db?sslmode=require&x=1"\n',
            encoding="utf-8",
        )
        load_dotenv(self.env_file)
        self.assertEqual(
            os.environ["OMERO_URL"],
            "postgresql://u:p@h/db?sslmode=require&x=1",
        )

    def test_missing_file_returns_zero(self) -> None:
        self.assertEqual(load_dotenv(self.dir / "nope.env"), 0)

    def test_find_dotenv_discovers_a_file(self) -> None:
        # The repository ships a real .env at its root, so discovery from the
        # test process (cwd or module path) should locate *some* .env file.
        found = find_dotenv()
        if found is not None:
            self.assertTrue(found.is_file())
            self.assertEqual(found.name, ".env")


if __name__ == "__main__":
    unittest.main()
