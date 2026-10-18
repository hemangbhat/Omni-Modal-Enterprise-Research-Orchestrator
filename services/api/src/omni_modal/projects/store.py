"""Projects and what belongs to them: linked documents, findings, notes,
reports and an activity log.

Every read and write is filtered by tenant, so another tenant's project id
behaves exactly like an unknown one (``ProjectNotFound``). Backed by Postgres
(migration 0013) when ``DATABASE_URL`` is set, else by a thread-safe
in-memory store.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace


class ProjectNotFound(Exception):
    pass


@dataclass(frozen=True)
class Project:
    id: str
    tenant_id: str
    name: str
    description: str
    created_by: str
    created_at: float = 0.0
    updated_at: float = 0.0
    archived_at: float | None = None

    def to_dict(self) -> dict:
        return {**asdict(self), "archived": self.archived_at is not None}


@dataclass(frozen=True)
class ProjectItem:
    id: str
    project_id: str
    tenant_id: str
    kind: str  # "finding" | "note"
    title: str
    body: str
    citations: list[dict] = field(default_factory=list)
    source_session_id: str | None = None
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProjectReport:
    id: str
    project_id: str | None
    tenant_id: str
    source: str  # "project" | "session"
    source_session_id: str | None
    title: str
    markdown: str
    created_by: str
    created_at: float = 0.0

    def to_dict(self, *, with_markdown: bool = True) -> dict:
        data = asdict(self)
        if not with_markdown:
            data.pop("markdown")
        return data


@dataclass(frozen=True)
class Activity:
    id: int
    project_id: str
    tenant_id: str
    actor: str
    action: str
    detail: dict
    created_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class InMemoryProjectStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._projects: dict[str, Project] = {}
        self._links: dict[tuple[str, str], dict] = {}  # (project_id, document_id) -> {tenant, added_by, added_at}
        self._items: dict[str, ProjectItem] = {}
        self._reports: dict[str, ProjectReport] = {}
        self._activity: list[Activity] = []
        self._ids = itertools.count(1)
        self._clock = 0.0

    def _now(self) -> float:
        self._clock = max(time.time(), self._clock + 1e-6)
        return self._clock

    # ── Projects ──────────────────────────────────────────────────────────
    def create_project(self, project: Project) -> Project:
        with self._lock:
            now = self._now()
            saved = replace(project, created_at=now, updated_at=now)
            self._projects[saved.id] = saved
            return saved

    def get_project(self, tenant_id: str, project_id: str) -> Project:
        with self._lock:
            project = self._projects.get(project_id)
            if project is None or project.tenant_id != tenant_id:
                raise ProjectNotFound(project_id)
            return project

    def list_projects(self, tenant_id: str) -> list[Project]:
        with self._lock:
            items = [p for p in self._projects.values() if p.tenant_id == tenant_id]
        return sorted(items, key=lambda p: p.updated_at, reverse=True)

    def update_project(self, tenant_id: str, project_id: str, **fields) -> Project:
        with self._lock:
            project = self.get_project(tenant_id, project_id)
            updated = replace(project, **fields, updated_at=self._now())
            self._projects[project_id] = updated
            return updated

    def touch(self, tenant_id: str, project_id: str) -> None:
        self.update_project(tenant_id, project_id)

    def list_with_counts(self, tenant_id: str) -> list[tuple[Project, dict]]:
        with self._lock:
            out = []
            for project in self.list_projects(tenant_id):
                items = [i for i in self._items.values() if i.project_id == project.id]
                out.append((project, {
                    "documents": sum(1 for (p, _d) in self._links if p == project.id),
                    "findings": sum(1 for i in items if i.kind == "finding"),
                    "notes": sum(1 for i in items if i.kind == "note"),
                    "reports": sum(1 for r in self._reports.values() if r.project_id == project.id),
                }))
            return out

    # ── Document links ───────────────────────────────────────────────────
    def link_document(self, tenant_id: str, project_id: str, document_id: str, user_id: str) -> bool:
        with self._lock:
            self.get_project(tenant_id, project_id)
            key = (project_id, document_id)
            if key in self._links:
                return False
            self._links[key] = {"tenant_id": tenant_id, "added_by": user_id, "added_at": self._now()}
            return True

    def unlink_document(self, tenant_id: str, project_id: str, document_id: str) -> bool:
        with self._lock:
            self.get_project(tenant_id, project_id)
            return self._links.pop((project_id, document_id), None) is not None

    def document_ids(self, tenant_id: str, project_id: str) -> list[str]:
        with self._lock:
            self.get_project(tenant_id, project_id)
            return [d for (p, d), link in self._links.items() if p == project_id and link["tenant_id"] == tenant_id]

    def projects_for_documents(self, tenant_id: str, document_ids: list[str]) -> dict[str, list[str]]:
        wanted = set(document_ids)
        out: dict[str, list[str]] = {}
        with self._lock:
            for (project_id, document_id), link in self._links.items():
                if link["tenant_id"] == tenant_id and document_id in wanted:
                    out.setdefault(document_id, []).append(project_id)
        return out

    def forget_document(self, tenant_id: str, document_id: str) -> None:
        """A deleted document leaves every project (ON DELETE CASCADE in Postgres)."""
        with self._lock:
            for key in [k for k, link in self._links.items() if k[1] == document_id and link["tenant_id"] == tenant_id]:
                del self._links[key]

    # ── Findings and notes ───────────────────────────────────────────────
    def add_item(self, item: ProjectItem) -> ProjectItem:
        with self._lock:
            self.get_project(item.tenant_id, item.project_id)
            now = self._now()
            saved = replace(item, created_at=now, updated_at=now)
            self._items[saved.id] = saved
            return saved

    def get_item(self, tenant_id: str, project_id: str, item_id: str) -> ProjectItem:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.tenant_id != tenant_id or item.project_id != project_id:
                raise ProjectNotFound(item_id)
            return item

    def list_items(self, tenant_id: str, project_id: str) -> list[ProjectItem]:
        with self._lock:
            self.get_project(tenant_id, project_id)
            items = [i for i in self._items.values() if i.project_id == project_id and i.tenant_id == tenant_id]
        return sorted(items, key=lambda i: i.created_at, reverse=True)

    def update_item(self, tenant_id: str, project_id: str, item_id: str, **fields) -> ProjectItem:
        with self._lock:
            item = self.get_item(tenant_id, project_id, item_id)
            updated = replace(item, **fields, updated_at=self._now())
            self._items[item_id] = updated
            return updated

    def delete_item(self, tenant_id: str, project_id: str, item_id: str) -> None:
        with self._lock:
            self.get_item(tenant_id, project_id, item_id)
            del self._items[item_id]

    # ── Reports ──────────────────────────────────────────────────────────
    def add_report(self, report: ProjectReport) -> ProjectReport:
        with self._lock:
            if report.project_id:
                self.get_project(report.tenant_id, report.project_id)
            saved = replace(report, created_at=self._now())
            self._reports[saved.id] = saved
            return saved

    def get_report(self, tenant_id: str, report_id: str) -> ProjectReport:
        with self._lock:
            report = self._reports.get(report_id)
            if report is None or report.tenant_id != tenant_id:
                raise ProjectNotFound(report_id)
            return report

    def list_reports(self, tenant_id: str, project_id: str) -> list[ProjectReport]:
        with self._lock:
            reports = [r for r in self._reports.values() if r.tenant_id == tenant_id and r.project_id == project_id]
        return sorted(reports, key=lambda r: r.created_at, reverse=True)

    def list_reports_by(self, tenant_id: str, user_id: str) -> list[ProjectReport]:
        with self._lock:
            reports = [r for r in self._reports.values() if r.tenant_id == tenant_id and r.created_by == user_id]
        return sorted(reports, key=lambda r: r.created_at, reverse=True)

    # ── Activity ─────────────────────────────────────────────────────────
    def log(self, tenant_id: str, project_id: str, actor: str, action: str, detail: dict | None = None) -> None:
        with self._lock:
            self._activity.append(Activity(next(self._ids), project_id, tenant_id, actor, action,
                                           dict(detail or {}), self._now()))

    def activity(self, tenant_id: str, project_id: str | None = None, limit: int = 50) -> list[Activity]:
        with self._lock:
            rows = [a for a in self._activity
                    if a.tenant_id == tenant_id and (project_id is None or a.project_id == project_id)]
        return sorted(rows, key=lambda a: a.id, reverse=True)[:limit]


def _ts(value) -> float | None:
    return value.timestamp() if value is not None else None


def _list(value) -> list:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value) if isinstance(value, list) else []


def _dict(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


_P = "id, tenant_id, name, description, created_by, created_at, updated_at, archived_at"
_I = ("id, project_id, tenant_id, kind, title, body, citations, source_session_id, "
      "created_by, created_at, updated_at")
_R = "id, project_id, tenant_id, source, source_session_id, title, markdown, created_by, created_at"
_A = "id, project_id, tenant_id, actor, action, detail, created_at"


class PostgresProjectStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    @staticmethod
    def _project(r) -> Project:
        return Project(r[0], r[1], r[2], r[3], r[4], _ts(r[5]) or 0.0, _ts(r[6]) or 0.0, _ts(r[7]))

    @staticmethod
    def _item(r) -> ProjectItem:
        return ProjectItem(r[0], r[1], r[2], r[3], r[4], r[5], _list(r[6]), r[7], r[8],
                           _ts(r[9]) or 0.0, _ts(r[10]) or 0.0)

    @staticmethod
    def _report(r) -> ProjectReport:
        return ProjectReport(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], _ts(r[8]) or 0.0)

    @staticmethod
    def _activity(r) -> Activity:
        return Activity(int(r[0]), r[1], r[2], r[3], r[4], _dict(r[5]), _ts(r[6]) or 0.0)

    def _one(self, sql: str, params: tuple):
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: tuple) -> list:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ── Projects ──────────────────────────────────────────────────────────
    def create_project(self, p: Project) -> Project:
        row = self._one(
            f"""INSERT INTO projects (id, tenant_id, name, description, created_by)
                VALUES (%s, %s, %s, %s, %s) RETURNING {_P}""",
            (p.id, p.tenant_id, p.name, p.description, p.created_by),
        )
        return self._project(row)

    def get_project(self, tenant_id: str, project_id: str) -> Project:
        row = self._one(f"SELECT {_P} FROM projects WHERE id = %s AND tenant_id = %s", (project_id, tenant_id))
        if row is None:
            raise ProjectNotFound(project_id)
        return self._project(row)

    def list_projects(self, tenant_id: str) -> list[Project]:
        rows = self._all(f"SELECT {_P} FROM projects WHERE tenant_id = %s ORDER BY updated_at DESC LIMIT 500",
                         (tenant_id,))
        return [self._project(r) for r in rows]

    def update_project(self, tenant_id: str, project_id: str, **fields) -> Project:
        unknown = set(fields) - {"name", "description", "archived_at"}
        if unknown:
            raise ValueError(f"Cannot update project fields: {sorted(unknown)}")
        sets, params = ["updated_at = now()"], []
        for name, value in fields.items():
            if name == "archived_at":
                sets.append("archived_at = CASE WHEN %s THEN now() ELSE NULL END")
                params.append(value is not None)
            else:
                sets.append(f"{name} = %s")
                params.append(value)
        row = self._one(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = %s AND tenant_id = %s RETURNING {_P}",
            (*params, project_id, tenant_id),
        )
        if row is None:
            raise ProjectNotFound(project_id)
        return self._project(row)

    def touch(self, tenant_id: str, project_id: str) -> None:
        self.update_project(tenant_id, project_id)

    def list_with_counts(self, tenant_id: str) -> list[tuple[Project, dict]]:
        rows = self._all(
            f"""SELECT {", ".join("p." + c.strip() for c in _P.split(","))},
                       (SELECT count(*) FROM project_documents pd WHERE pd.project_id = p.id),
                       (SELECT count(*) FROM project_items i WHERE i.project_id = p.id AND i.kind = 'finding'),
                       (SELECT count(*) FROM project_items i WHERE i.project_id = p.id AND i.kind = 'note'),
                       (SELECT count(*) FROM project_reports r WHERE r.project_id = p.id)
                FROM projects p WHERE p.tenant_id = %s ORDER BY p.updated_at DESC LIMIT 500""",
            (tenant_id,),
        )
        return [
            (self._project(r[:8]), {"documents": int(r[8]), "findings": int(r[9]), "notes": int(r[10]),
                                    "reports": int(r[11])})
            for r in rows
        ]

    # ── Document links ───────────────────────────────────────────────────
    def link_document(self, tenant_id: str, project_id: str, document_id: str, user_id: str) -> bool:
        self.get_project(tenant_id, project_id)
        # The document must be the same tenant's: the SELECT finds nothing otherwise.
        row = self._one(
            """INSERT INTO project_documents (project_id, document_id, tenant_id, added_by)
               SELECT %s, d.id, %s, %s FROM documents d WHERE d.id::text = %s AND d.tenant_id = %s
               ON CONFLICT DO NOTHING RETURNING document_id""",
            (project_id, tenant_id, user_id, document_id, tenant_id),
        )
        return row is not None

    def unlink_document(self, tenant_id: str, project_id: str, document_id: str) -> bool:
        self.get_project(tenant_id, project_id)
        row = self._one(
            """DELETE FROM project_documents WHERE project_id = %s AND document_id::text = %s AND tenant_id = %s
               RETURNING document_id""",
            (project_id, document_id, tenant_id),
        )
        return row is not None

    def document_ids(self, tenant_id: str, project_id: str) -> list[str]:
        self.get_project(tenant_id, project_id)
        rows = self._all(
            "SELECT document_id::text FROM project_documents WHERE project_id = %s AND tenant_id = %s",
            (project_id, tenant_id),
        )
        return [r[0] for r in rows]

    def projects_for_documents(self, tenant_id: str, document_ids: list[str]) -> dict[str, list[str]]:
        if not document_ids:
            return {}
        rows = self._all(
            """SELECT document_id::text, project_id FROM project_documents
               WHERE tenant_id = %s AND document_id::text = ANY(%s)""",
            (tenant_id, list(document_ids)),
        )
        out: dict[str, list[str]] = {}
        for document_id, project_id in rows:
            out.setdefault(document_id, []).append(project_id)
        return out

    def forget_document(self, tenant_id: str, document_id: str) -> None:
        pass  # ON DELETE CASCADE

    # ── Findings and notes ───────────────────────────────────────────────
    def add_item(self, i: ProjectItem) -> ProjectItem:
        self.get_project(i.tenant_id, i.project_id)
        row = self._one(
            f"""INSERT INTO project_items (id, project_id, tenant_id, kind, title, body, citations,
                                           source_session_id, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s) RETURNING {_I}""",
            (i.id, i.project_id, i.tenant_id, i.kind, i.title, i.body, json.dumps(i.citations),
             i.source_session_id, i.created_by),
        )
        return self._item(row)

    def get_item(self, tenant_id: str, project_id: str, item_id: str) -> ProjectItem:
        row = self._one(f"SELECT {_I} FROM project_items WHERE id = %s AND tenant_id = %s AND project_id = %s",
                        (item_id, tenant_id, project_id))
        if row is None:
            raise ProjectNotFound(item_id)
        return self._item(row)

    def list_items(self, tenant_id: str, project_id: str) -> list[ProjectItem]:
        self.get_project(tenant_id, project_id)
        rows = self._all(
            f"SELECT {_I} FROM project_items WHERE project_id = %s AND tenant_id = %s ORDER BY created_at DESC",
            (project_id, tenant_id),
        )
        return [self._item(r) for r in rows]

    def update_item(self, tenant_id: str, project_id: str, item_id: str, **fields) -> ProjectItem:
        unknown = set(fields) - {"title", "body"}
        if unknown:
            raise ValueError(f"Cannot update item fields: {sorted(unknown)}")
        if not fields:
            return self.get_item(tenant_id, project_id, item_id)
        sets = ", ".join(f"{name} = %s" for name in fields)
        row = self._one(
            f"""UPDATE project_items SET {sets}, updated_at = now()
                WHERE id = %s AND tenant_id = %s AND project_id = %s RETURNING {_I}""",
            (*fields.values(), item_id, tenant_id, project_id),
        )
        if row is None:
            raise ProjectNotFound(item_id)
        return self._item(row)

    def delete_item(self, tenant_id: str, project_id: str, item_id: str) -> None:
        row = self._one(
            "DELETE FROM project_items WHERE id = %s AND tenant_id = %s AND project_id = %s RETURNING id",
            (item_id, tenant_id, project_id),
        )
        if row is None:
            raise ProjectNotFound(item_id)

    # ── Reports ──────────────────────────────────────────────────────────
    def add_report(self, r: ProjectReport) -> ProjectReport:
        if r.project_id:
            self.get_project(r.tenant_id, r.project_id)
        row = self._one(
            f"""INSERT INTO project_reports (id, project_id, tenant_id, source, source_session_id, title,
                                             markdown, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_R}""",
            (r.id, r.project_id, r.tenant_id, r.source, r.source_session_id, r.title, r.markdown, r.created_by),
        )
        return self._report(row)

    def get_report(self, tenant_id: str, report_id: str) -> ProjectReport:
        row = self._one(f"SELECT {_R} FROM project_reports WHERE id = %s AND tenant_id = %s", (report_id, tenant_id))
        if row is None:
            raise ProjectNotFound(report_id)
        return self._report(row)

    def list_reports(self, tenant_id: str, project_id: str) -> list[ProjectReport]:
        rows = self._all(
            f"""SELECT {_R} FROM project_reports WHERE tenant_id = %s AND project_id = %s
                ORDER BY created_at DESC LIMIT 200""",
            (tenant_id, project_id),
        )
        return [self._report(r) for r in rows]

    def list_reports_by(self, tenant_id: str, user_id: str) -> list[ProjectReport]:
        rows = self._all(
            f"""SELECT {_R} FROM project_reports WHERE tenant_id = %s AND created_by = %s
                ORDER BY created_at DESC LIMIT 200""",
            (tenant_id, user_id),
        )
        return [self._report(r) for r in rows]

    # ── Activity ─────────────────────────────────────────────────────────
    def log(self, tenant_id: str, project_id: str, actor: str, action: str, detail: dict | None = None) -> None:
        self._one(
            """INSERT INTO project_activity (project_id, tenant_id, actor, action, detail)
               VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id""",
            (project_id, tenant_id, actor, action, json.dumps(detail or {})),
        )

    def activity(self, tenant_id: str, project_id: str | None = None, limit: int = 50) -> list[Activity]:
        rows = self._all(
            f"""SELECT {_A} FROM project_activity
                WHERE tenant_id = %s AND (%s::text IS NULL OR project_id = %s)
                ORDER BY id DESC LIMIT %s""",
            (tenant_id, project_id, project_id, max(1, min(limit, 200))),
        )
        return [self._activity(r) for r in rows]


_in_memory: InMemoryProjectStore | None = None
_lock = threading.Lock()


def new_id() -> str:
    return uuid.uuid4().hex


def select_project_store():
    """Postgres-backed when configured (projects survive restarts), else in-process."""
    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return PostgresProjectStore(get_connection_pool())
        except Exception as exc:  # noqa: BLE001 - fall back to the in-process store
            from omni_modal.observability import observability  # noqa: PLC0415

            observability.capture_message(
                f"DATABASE_URL is set but the project store could not use Postgres ({exc}); "
                "projects will NOT survive a restart.",
                operation="projects.store.select", level="error",
            )
    global _in_memory
    with _lock:
        if _in_memory is None:
            _in_memory = InMemoryProjectStore()
        return _in_memory
