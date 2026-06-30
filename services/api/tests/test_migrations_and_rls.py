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


def test_rls_disabled_by_default(monkeypatch):
    from omni_modal.db import rls

    monkeypatch.delenv("RLS_ENFORCEMENT", raising=False)
    assert rls.rls_enabled() is False


def test_rls_enabled_via_env(monkeypatch):
    from omni_modal.db import rls

    monkeypatch.setenv("RLS_ENFORCEMENT", "true")
    assert rls.rls_enabled() is True


def test_apply_tenant_noop_when_disabled(monkeypatch):
    from omni_modal.db.rls import apply_tenant

    monkeypatch.delenv("RLS_ENFORCEMENT", raising=False)
    conn = _FakeConn()
    apply_tenant(conn, "t1")
    assert conn.calls == []  # no GUC set when enforcement is off


def test_apply_tenant_sets_guc_when_enabled(monkeypatch):
    from omni_modal.db.rls import apply_tenant

    monkeypatch.setenv("RLS_ENFORCEMENT", "true")
    conn = _FakeConn()
    apply_tenant(conn, "t1")
    assert len(conn.calls) == 1
    assert conn.calls[0][1] == ("t1", True)


def test_apply_tenant_ignores_empty_tenant(monkeypatch):
    from omni_modal.db.rls import apply_tenant

    monkeypatch.setenv("RLS_ENFORCEMENT", "true")
    conn = _FakeConn()
    apply_tenant(conn, "")
    assert conn.calls == []


# ── Retrieval path binds the tenant GUC when RLS is enabled ─────────────────
class _RlsCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, params))

    def fetchall(self):
        return []


class _RlsConn:
    def __init__(self):
        self.calls: list = []

    def transaction(self):
        outer = self

        class _T:
            def __enter__(self_):
                return outer

            def __exit__(self_, *a):
                return False

        return _T()

    def cursor(self, row_factory=None):
        return _RlsCursor(self.calls)


class _RlsPool:
    def __init__(self):
        self.conn = _RlsConn()

    def connection(self):
        conn = self.conn

        class _C:
            def __enter__(self_):
                return conn

            def __exit__(self_, *a):
                return False

        return _C()


class _FakeProvider:
    def embed_query(self, _q: str):
        return [0.1, 0.2, 0.3]


def test_retrieval_binds_tenant_guc_when_rls_enabled(monkeypatch):
    monkeypatch.setenv("RLS_ENFORCEMENT", "true")
    from omni_modal.qa.models import QueryRequest
    from omni_modal.qa.retrieval import PgVectorChunkRetriever

    pool = _RlsPool()
    retriever = PgVectorChunkRetriever(_FakeProvider(), pool=pool)
    req = QueryRequest(tenant_id="tenant-rls", user_id="u1", question="hello", top_k=5)
    rows = retriever.retrieve(req)
    assert rows == []
    # The GUC must have been set with the request's tenant inside the txn.
    assert any(
        "set_config('app.tenant_id'" in sql and params == ("tenant-rls", True)
        for sql, params in pool.conn.calls
    )


def test_retrieval_skips_guc_when_rls_disabled(monkeypatch):
    monkeypatch.delenv("RLS_ENFORCEMENT", raising=False)
    from omni_modal.qa.models import QueryRequest
    from omni_modal.qa.retrieval import PgVectorChunkRetriever

    pool = _RlsPool()
    retriever = PgVectorChunkRetriever(_FakeProvider(), pool=pool)
    req = QueryRequest(tenant_id="tenant-rls", user_id="u1", question="hello", top_k=5)
    retriever.retrieve(req)
    assert not any("set_config" in sql for sql, _ in pool.conn.calls)
