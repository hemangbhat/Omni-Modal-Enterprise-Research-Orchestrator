"""Tests for Phase 3 data hardening: migration runner logic + RLS helper.

The DB-touching parts of the runner are exercised against real Postgres in
deploy; here we unit-test the pure discovery/ordering/pending logic and the
RLS GUC-binding SQL without a database.
"""

from __future__ import annotations

import importlib.util
import re
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


def test_auth_recovery_migration_follows_rls_and_defines_both_tables():
    migrate = _load_migrate()
    files = migrate.discover_migrations(migrate.MIGRATIONS_DIR)
    names = [p.name for p in files]
    assert names.index("0008_rls.sql") < names.index("0009_auth_recovery.sql")
    sql = next(p for p in files if p.name == "0009_auth_recovery.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS user_mfa" in sql
    assert "CREATE TABLE IF NOT EXISTS password_reset_tokens" in sql
    assert "CREATE POLICY tenant_isolation ON user_mfa" in sql


def _array_tables(sql: str) -> set[str]:
    """Table names listed in a migration's `tenant_tables text[] := ARRAY[...]`."""
    body = re.search(r"tenant_tables\s+text\[\]\s*:=\s*ARRAY\[(.*?)\];", sql, re.S)
    assert body, "migration has no tenant_tables array"
    no_comments = re.sub(r"--[^\n]*", "", body.group(1))
    return set(re.findall(r"'(\w+)'", no_comments))


def test_empty_guc_migration_rebuilds_every_earlier_rls_policy_with_nullif():
    # After a SET LOCAL transaction commits, a pooled connection reports
    # app.tenant_id as '' rather than NULL; 0008/0009's `IS NULL` escape then
    # stops matching. 0011 must recreate every one of those policies so that
    # '' also counts as "no tenant bound".
    migrate = _load_migrate()
    files = migrate.discover_migrations(migrate.MIGRATIONS_DIR)
    names = [p.name for p in files]
    assert "0011_rls_empty_guc.sql" in names
    assert names.index("0010_research_sessions.sql") < names.index("0011_rls_empty_guc.sql")

    def read(name: str) -> str:
        return next(p for p in files if p.name == name).read_text(encoding="utf-8")

    earlier = _array_tables(read("0008_rls.sql")) | set(
        re.findall(r"CREATE POLICY tenant_isolation ON (\w+)", read("0009_auth_recovery.sql"))
    )
    assert "users" in earlier and "user_mfa" in earlier

    sql = read("0011_rls_empty_guc.sql")
    assert _array_tables(sql) >= earlier
    nullif = "NULLIF(current_setting(''app.tenant_id'', true), '''') IS NULL"
    assert f"USING ({nullif}" in sql
    assert f"WITH CHECK ({nullif}" in sql
    # No policy body may keep the bare NULL test that misses the empty string.
    assert "'USING (current_setting" not in sql
    assert "WITH CHECK (current_setting" not in sql


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


# ── Baseline guard: never replay migrations over a hand-migrated database ────
def _names(*names: str) -> list[Path]:
    return [Path(n) for n in names]


def test_baseline_names_cover_everything_up_to_and_including_the_target():
    migrate = _load_migrate()
    files = _names("0001_a.sql", "0002_b.sql", "0003_c.sql")
    assert migrate.baseline_names(files, "0002_b.sql") == ["0001_a.sql", "0002_b.sql"]
    assert migrate.baseline_names(files, "0003_c.sql") == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]


def test_baseline_names_rejects_an_unknown_target():
    migrate = _load_migrate()
    try:
        migrate.baseline_names(_names("0001_a.sql"), "0009_missing.sql")
    except ValueError as exc:
        assert "0009_missing.sql" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_requires_baseline_when_tables_exist_but_no_history():
    migrate = _load_migrate()
    # Schema created by hand: replaying 0001.. would re-run destructive steps
    # (0004 truncates the embeddings table), so the runner must refuse.
    assert migrate.requires_baseline(applied=set(), existing_tables={"documents", "embeddings"})
    assert migrate.requires_baseline(applied=set(), existing_tables={"users", "schema_migrations"})


def test_no_baseline_needed_for_an_empty_or_tracked_database():
    migrate = _load_migrate()
    assert not migrate.requires_baseline(applied=set(), existing_tables=set())
    assert not migrate.requires_baseline(applied=set(), existing_tables={"schema_migrations"})
    assert not migrate.requires_baseline(applied={"0001_initial.sql"}, existing_tables={"documents"})


def test_the_destructive_migration_is_still_the_reason_for_the_guard():
    migrate = _load_migrate()
    sql = (migrate.MIGRATIONS_DIR / "0004_embeddings_384.sql").read_text(encoding="utf-8")
    assert "TRUNCATE TABLE embeddings" in sql
