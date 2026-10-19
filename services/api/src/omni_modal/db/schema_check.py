"""Refuse to start against a database that is missing migrations this code needs.

Without this, a deploy that runs before ``python scripts/migrate.py`` fails
deep inside some query ("column ... does not exist"). This check fails first,
with one plain sentence naming what to run. The SQL files are not in the API
image, so the newest required migration is named here; a test keeps it equal
to the newest file in ``packages/db/drizzle``.
"""

from __future__ import annotations

REQUIRED_MIGRATION = "0015_external_research.sql"


class SchemaOutOfDate(RuntimeError):
    pass


def assert_schema_current(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
        has_table = bool(cur.fetchone()[0])
        applied: set[str] = set()
        if has_table:
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
    if REQUIRED_MIGRATION not in applied:
        raise SchemaOutOfDate(
            f"The database is missing migration {REQUIRED_MIGRATION} (and possibly earlier ones). "
            "Run `python scripts/migrate.py` with this service's DATABASE_URL, then deploy again."
        )
