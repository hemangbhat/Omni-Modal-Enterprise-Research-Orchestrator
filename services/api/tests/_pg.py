"""Opt-in Postgres for tests: only ``OMERO_TEST_DATABASE_URL``, never ``DATABASE_URL``.

Point it at a disposable database (CI uses a pgvector service container;
locally ``docker run -p 5433:5432 -e POSTGRES_PASSWORD=omero pgvector/pgvector:pg16``).
A hosted database is refused, so a test run can never write to a real workspace.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db"}


def disposable_pg_url() -> str | None:
    url = os.environ.get("OMERO_TEST_DATABASE_URL", "").strip()
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        raise RuntimeError(f"OMERO_TEST_DATABASE_URL must point at a local disposable database, not {host!r}.")
    return url


def migrate(url: str) -> None:
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        import migrate as runner  # noqa: PLC0415
    finally:
        sys.path.remove(str(scripts))
    runner.run(url)  # applies what is pending; raises on a failed migration
