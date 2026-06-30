"""Tests for Phase 3 data hardening: migration runner logic + RLS helper.

The DB-touching parts of the runner are exercised against real Postgres in
deploy; here we unit-test the pure discovery/ordering/pending logic and the
RLS GUC-binding SQL without a database.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import _path  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATE_PY = _REPO_ROOT / "scripts" / "migrate.py"


def _load_migrate():
    spec = importlib.util.spec_from_file_location("omero_migrate", _MIGRATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_discover_migrations_is_ordered(tmp_path: Path):
    migrate = _load_migrate()
    # Create out of order; expect lexicographic (numeric-prefix) ordering.
    for name in ["0003_c.sql", "0001_a.sql", "0002_b.sql", "notes.txt"]:
        (tmp_path / name).write_text("select 1;", encoding="utf-8")
    found = migrate.discover_migrations(tmp_path)
    assert [p.name for p in found] == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]


def test_pending_excludes_applied(tmp_path: Path):
    migrate = _load_migrate()
    for name in ["0001_a.sql", "0002_b.sql", "0003_c.sql"]:
        (tmp_path / name).write_text("select 1;", encoding="utf-8")
    all_files = migrate.discover_migrations(tmp_path)
    pending = migrate.pending_migrations(all_files, {"0001_a.sql", "0002_b.sql"})
    assert [p.name for p in pending] == ["0003_c.sql"]


def test_pending_empty_when_all_applied(tmp_path: Path):
    migrate = _load_migrate()
    (tmp_path / "0001_a.sql").write_text("select 1;", encoding="utf-8")
    all_files = migrate.discover_migrations(tmp_path)
    assert migrate.pending_migrations(all_files, {"0001_a.sql"}) == []


def test_real_migrations_dir_includes_rls():
    migrate = _load_migrate()
    names = [p.name for p in migrate.discover_migrations(migrate.MIGRATIONS_DIR)]
    assert "0001_initial.sql" in names
    assert "0008_rls.sql" in names
    # Ordering keeps the baseline first and RLS last of the known set.
    assert names.index("0001_initial.sql") < names.index("0008_rls.sql")


# ── RLS helper ──────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, params))


class _FakeConn:
    def __init__(self):
        self.calls: list = []

    def cursor(self):
        return _FakeCursor(self.calls)


def test_set_tenant_uses_set_config_with_params():
    from omni_modal.db.rls import set_tenant

    conn = _FakeConn()
    set_tenant(conn, "tenant-xyz")
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "set_config('app.tenant_id'" in sql
    assert params == ("tenant-xyz", True)


def test_set_tenant_non_local():
    from omni_modal.db.rls import set_tenant

    conn = _FakeConn()
    set_tenant(conn, "t1", local=False)
    assert conn.calls[0][1] == ("t1", False)
