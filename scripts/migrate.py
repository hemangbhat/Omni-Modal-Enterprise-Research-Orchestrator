"""Idempotent SQL migration runner (Phase 3 — migrations as code on deploy).

Applies the ordered ``packages/db/drizzle/*.sql`` files against ``DATABASE_URL``
and records each in a ``schema_migrations`` table so re-runs are safe. This is
what replaces the previous "apply migrations by hand in the Neon console" step.

Use as a deploy release command:

    python scripts/migrate.py            # apply all pending migrations
    python scripts/migrate.py --dry-run  # list what would be applied

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


def run(database_url: str, *, dry_run: bool = False) -> int:
    """Apply pending migrations. Returns the number applied (or that would be)."""
    import psycopg  # type: ignore[import-not-found]

    all_files = discover_migrations(MIGRATIONS_DIR)
    if not all_files:
        print(f"No migrations found in {MIGRATIONS_DIR}")
        return 0

    with psycopg.connect(database_url, autocommit=True) as conn:
        _ensure_table(conn)
        applied = _applied_set(conn)
        pending = pending_migrations(all_files, applied)

        if not pending:
            print(f"Up to date — {len(applied)} migration(s) already applied.")
            return 0

        print(f"{len(pending)} pending migration(s):")
        for path in pending:
            print(f"  - {path.name}")
        if dry_run:
            return len(pending)

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
        run(database_url, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
