"""The API names the migration it needs, and that name tracks the newest SQL file."""

from __future__ import annotations

import unittest
from pathlib import Path

import _path  # noqa: F401

from omni_modal.db.schema_check import REQUIRED_MIGRATION, SchemaOutOfDate, assert_schema_current

DRIZZLE = Path(__file__).resolve().parents[3] / "packages" / "db" / "drizzle"


class _Conn:
    def __init__(self, has_table: bool, applied: list[str]) -> None:
        self.results = [[(has_table,)], [(name,) for name in applied]]

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def execute(self, sql, params=None):
                self._rows = conn.results.pop(0)

            def fetchone(self):
                return self._rows[0]

            def fetchall(self):
                return self._rows

        return _Cur()


class SchemaCheckTests(unittest.TestCase):
    def test_required_is_the_newest_migration_file(self) -> None:
        newest = sorted(p.name for p in DRIZZLE.glob("*.sql"))[-1]
        self.assertEqual(REQUIRED_MIGRATION, newest, "Update REQUIRED_MIGRATION in db/schema_check.py")

    def test_current_schema_passes(self) -> None:
        assert_schema_current(_Conn(True, ["0014_x.sql", REQUIRED_MIGRATION]))

    def test_missing_migration_names_the_fix(self) -> None:
        with self.assertRaises(SchemaOutOfDate) as ctx:
            assert_schema_current(_Conn(True, ["0013_projects.sql"]))
        self.assertIn("scripts/migrate.py", str(ctx.exception))
        with self.assertRaises(SchemaOutOfDate):
            assert_schema_current(_Conn(False, []))


if __name__ == "__main__":
    unittest.main()
