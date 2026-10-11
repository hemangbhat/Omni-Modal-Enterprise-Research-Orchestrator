"""Idempotent SQL migration runner (Phase 3 — migrations as code on deploy).

Applies the ordered ``packages/db/drizzle/*.sql`` files against ``DATABASE_URL``
and records each in a ``schema_migrations`` table so re-runs are safe. This is
what replaces the previous "apply migrations by hand in the Neon console" step.

Use as a deploy release command:

    python scripts/migrate.py            # apply all pending migrations
    python scripts/migrate.py --dry-run  # list what would be applied

Adopting a database whose schema was created by hand (tables exist, but there
is no ``schema_migrations`` history): the runner refuses to proceed, because
replaying old files is not safe (``0004_embeddings_384.sql`` truncates the
embeddings table). Check which files are already reflected in the schema, then
record them without executing them:

    python scripts/migrate.py --baseline-through 0007_audit_events.sql --dry-run
    python scripts/migrate.py --baseline-through 0007_audit_events.sql

Render: wire this as a `preDeployCommand` so the schema is always current before
new code serves traffic (see render.yaml).

Design:
  * Discovery + ordering + pending-set computation are pure functions, unit
    tested without a database.
  * Each migration runs in autocommit so statements that cannot run inside a
    transaction block still work; the filename is then recorded.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "packages" / "db" / "drizzle"


def discover_migrations(directory: Path) -> list[Path]:
    """Return all ``*.sql`` files in lexicographic (= numeric prefix) order."""
    return sorted(p for p in directory.glob("*.sql") if p.is_file())


def pending_migrations(all_files: list[Path], applied: set[str]) -> list[Path]:
    """Migrations not yet recorded as applied, preserving order."""
    return [p for p in all_files if p.name not in applied]


def baseline_names(all_files: list[Path], through: str) -> list[str]:
    """Names of every migration up to and including ``through``, in order."""
    names = [p.name for p in all_files]
    if through not in names:
        raise ValueError(f"Unknown migration {through!r}; expected one of: {', '.join(names)}")
    return names[: names.index(through) + 1]


def requires_baseline(applied: set[str], existing_tables: set[str]) -> bool:
    """True when the database already has tables but no migration history.

    Such a schema was built outside this runner. Applying every file from the
    start would re-run steps that are not safe to repeat, so the operator must
    first say which files are already in place (``--baseline-through``).
    """
    return not applied and bool(existing_tables - {"schema_migrations"})


def _existing_tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        return {row[0] for row in cur.fetchall()}


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   filename text PRIMARY KEY,
                   applied_at timestamptz NOT NULL DEFAULT now()
               )"""
        )


def _applied_set(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def run(database_url: str, *, dry_run: bool = False, baseline_through: str | None = None) -> int:
    """Apply pending migrations. Returns the number applied (or that would be).

    A dry run reads the database but writes nothing, not even the
    ``schema_migrations`` table.
    """
    import psycopg  # type: ignore[import-not-found]

    all_files = discover_migrations(MIGRATIONS_DIR)
    if not all_files:
        print(f"No migrations found in {MIGRATIONS_DIR}")
        return 0

    with psycopg.connect(database_url, autocommit=True) as conn:
        tables = _existing_tables(conn)
        applied = _applied_set(conn) if "schema_migrations" in tables else set()

        if baseline_through is not None:
            if applied:
                raise RuntimeError(
                    "This database already has a migration history; --baseline-through "
                    "is only for adopting a schema that was created by hand."
                )
            baseline = baseline_names(all_files, baseline_through)
            print(f"Recording {len(baseline)} migration(s) as already applied (not executed):")
            for name in baseline:
                print(f"  = {name}")
            if not dry_run:
                _ensure_table(conn)
                with conn.cursor() as cur:
                    for name in baseline:
                        cur.execute(
                            "INSERT INTO schema_migrations (filename) VALUES (%s) "
                            "ON CONFLICT (filename) DO NOTHING",
                            (name,),
                        )
            applied = set(baseline)
        elif requires_baseline(applied, tables):
            raise RuntimeError(
                "This database already has tables but no migration history. Applying every "
                "migration from the start is not safe (0004 truncates the embeddings table). "
                "Check which migrations are already reflected in the schema, then run again "
                "with --baseline-through <last applied file>."
            )

        pending = pending_migrations(all_files, applied)

        if not pending:
            print(f"Up to date — {len(applied)} migration(s) already applied.")
            return 0

        print(f"{len(pending)} pending migration(s):")
        for path in pending:
            print(f"  - {path.name}")
        if dry_run:
            print("Dry run: nothing was changed.")
            return len(pending)

        _ensure_table(conn)
        for path in pending:
            sql = path.read_text(encoding="utf-8")
            print(f"Applying {path.name} ...", flush=True)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s) "
                    "ON CONFLICT (filename) DO NOTHING",
                    (path.name,),
                )
        print(f"Done — applied {len(pending)} migration(s).")
        return len(pending)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument("--dry-run", action="store_true", help="List pending migrations without applying.")
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL.")
    parser.add_argument(
        "--baseline-through", default=None, metavar="FILENAME",
        help="Adopt a hand-built schema: record migrations up to and including "
             "FILENAME as applied without executing them, then apply the rest.",
    )
    args = parser.parse_args()

    # Load .env so local runs pick up DATABASE_URL like the server does.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api" / "src"))
        from omni_modal.env_loader import load_dotenv  # noqa: PLC0415

        load_dotenv()
    except Exception:
        pass

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; nothing to migrate (in-memory mode).")
        return 0

    try:
        run(database_url, dry_run=args.dry_run, baseline_through=args.baseline_through)
    except Exception as exc:  # noqa: BLE001
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
