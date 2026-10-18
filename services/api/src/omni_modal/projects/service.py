"""Projects: the place where documents, research, findings and reports come together.

Projects are shared by everyone in a tenant. Researchers and admins change
them (enforced by the API's role checks); archiving and restoring is for the
creator or an admin; editing or deleting a finding or note is for its author
or an admin. An archived project is read-only until restored.
"""

from __future__ import annotations

import time

from omni_modal.documents.service import Forbidden, InvalidState
from omni_modal.documents.store import DocumentNotFound
from omni_modal.projects import reports
from omni_modal.projects.store import Project, ProjectItem, ProjectNotFound, ProjectReport, new_id

NAME_MAX = 120
DESCRIPTION_MAX = 2000
TITLE_MAX = 300
BODY_MAX = 20_000
MAX_CITATIONS = 50
_CITATION_FIELDS = (
    "document_id", "chunk_id", "title", "source_type", "chunk_index", "page_number",
    "segment_index", "start_ms", "end_ms", "snippet", "scope", "cited",
)
_ITEM_KINDS = ("finding", "note")


class ProjectRejected(ValueError):
    """A request the user can fix (empty name, unknown kind, too many citations)."""


def _clean(text: str | None, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def _clean_citation(c: dict) -> dict:
    out = {k: c.get(k) for k in _CITATION_FIELDS}
    out["snippet"] = str(out.get("snippet") or "")[:500]
    out["title"] = str(out.get("title") or "")[:300]
    return out


class ProjectService:
    def __init__(self, *, store, documents, sessions, entities=None) -> None:
        self._store = store
        self._documents = documents
        self._sessions = sessions
        self._entities = entities

    # ── Projects ─────────────────────────────────────────────────────────
    def create(self, tenant_id: str, user_id: str, *, name: str, description: str = "") -> Project:
        name = _clean(name, NAME_MAX)
        if not name:
            raise ProjectRejected("Give the project a name.")
        project = self._store.create_project(Project(
            id=new_id(), tenant_id=tenant_id, name=name,
            description=(description or "").strip()[:DESCRIPTION_MAX], created_by=user_id,
        ))
        self._store.log(tenant_id, project.id, user_id, "project_created", {"name": name})
        return project

    def get(self, tenant_id: str, project_id: str) -> Project:
        return self._store.get_project(tenant_id, project_id)

    def list(self, tenant_id: str, *, include_archived: bool = False) -> list[dict]:
        """Projects with their counts, in one store call."""
        return [
            {"project": project, "counts": counts}
            for project, counts in self._store.list_with_counts(tenant_id)
            if include_archived or project.archived_at is None
        ]

    def projects(self, tenant_id: str, *, include_archived: bool = False) -> list[Project]:
        """Projects without counts (cheap: names for lists, search, the dashboard)."""
        return [p for p in self._store.list_projects(tenant_id) if include_archived or p.archived_at is None]

    def _writable(self, tenant_id: str, project_id: str) -> Project:
        project = self._store.get_project(tenant_id, project_id)
        if project.archived_at is not None:
            raise InvalidState("This project is archived. Restore it to make changes.")
        return project

    def update(self, tenant_id: str, user_id: str, project_id: str, *,
               name: str | None = None, description: str | None = None) -> Project:
        self._writable(tenant_id, project_id)
        fields = {}
        if name is not None:
            fields["name"] = _clean(name, NAME_MAX)
            if not fields["name"]:
                raise ProjectRejected("Give the project a name.")
        if description is not None:
            fields["description"] = description.strip()[:DESCRIPTION_MAX]
        project = self._store.update_project(tenant_id, project_id, **fields)
        self._store.log(tenant_id, project_id, user_id, "project_updated", {k: v for k, v in fields.items() if k == "name"})
        return project

    @staticmethod
    def _creator_or_admin(project: Project, user_id: str, roles) -> None:
        if project.created_by != user_id and "admin" not in roles:
            raise Forbidden("Only the person who created this project, or an admin, can do that.")

    def archive(self, tenant_id: str, user_id: str, roles, project_id: str) -> Project:
        project = self._store.get_project(tenant_id, project_id)
        self._creator_or_admin(project, user_id, roles)
        updated = self._store.update_project(tenant_id, project_id, archived_at=time.time())
        self._store.log(tenant_id, project_id, user_id, "project_archived")
        return updated

    def restore(self, tenant_id: str, user_id: str, roles, project_id: str) -> Project:
        project = self._store.get_project(tenant_id, project_id)
        self._creator_or_admin(project, user_id, roles)
        updated = self._store.update_project(tenant_id, project_id, archived_at=None)
        self._store.log(tenant_id, project_id, user_id, "project_restored")
        return updated

    # ── Documents ────────────────────────────────────────────────────────
    def add_document(self, tenant_id: str, user_id: str, project_id: str, document_id: str) -> None:
        self._writable(tenant_id, project_id)
        doc, _ = self._documents.get(tenant_id, document_id)  # DocumentNotFound for another tenant's id
        if self._store.link_document(tenant_id, project_id, doc.id, user_id):
            self._store.touch(tenant_id, project_id)
            self._store.log(tenant_id, project_id, user_id, "document_added",
                            {"document_id": doc.id, "title": doc.title})

    def remove_document(self, tenant_id: str, user_id: str, project_id: str, document_id: str) -> None:
        self._writable(tenant_id, project_id)
        if self._store.unlink_document(tenant_id, project_id, document_id):
            self._store.touch(tenant_id, project_id)
            self._store.log(tenant_id, project_id, user_id, "document_removed", {"document_id": document_id})

    def documents(self, tenant_id: str, project_id: str) -> list:
        docs = []
        for document_id in self._store.document_ids(tenant_id, project_id):
            try:
                docs.append(self._documents.get(tenant_id, document_id)[0])
            except DocumentNotFound:
                continue
        return docs

    def scope(self, tenant_id: str, project_id: str) -> tuple[str, ...]:
        """The documents research in this project may draw on."""
        return tuple(self._store.document_ids(tenant_id, project_id))

    def projects_for_documents(self, tenant_id: str, document_ids: list[str]) -> dict[str, list[str]]:
        return self._store.projects_for_documents(tenant_id, document_ids)

    def forget_document(self, tenant_id: str, document_id: str) -> None:
        self._store.forget_document(tenant_id, document_id)

    # ── Research sessions ────────────────────────────────────────────────
    def link_session(self, tenant_id: str, user_id: str, project_id: str | None, session_id: str):
        if project_id is not None:
            self._writable(tenant_id, project_id)
        summary = self._sessions.set_project(tenant_id, user_id, session_id, project_id)
        if project_id is not None:
            self._store.log(tenant_id, project_id, user_id, "session_linked", {"title": summary.title})
        return summary

    def sessions(self, tenant_id: str, user_id: str, project_id: str) -> list:
        self._store.get_project(tenant_id, project_id)
        return self._sessions.list(tenant_id, user_id, kind="research", project_id=project_id)

    # ── Findings and notes ───────────────────────────────────────────────
    def add_item(self, tenant_id: str, user_id: str, project_id: str, *, kind: str, title: str,
                 body: str = "", citations: list[dict] | None = None,
                 source_session_id: str | None = None) -> ProjectItem:
        if kind not in _ITEM_KINDS:
            raise ProjectRejected("kind must be finding or note.")
        title = _clean(title, TITLE_MAX)
        if not title:
            raise ProjectRejected("Give it a title.")
        citations = list(citations or [])
        if len(citations) > MAX_CITATIONS:
            raise ProjectRejected(f"A finding can keep at most {MAX_CITATIONS} sources.")
        self._writable(tenant_id, project_id)
        item = self._store.add_item(ProjectItem(
            id=new_id(), project_id=project_id, tenant_id=tenant_id, kind=kind, title=title,
            body=reports.answer_text(body)[:BODY_MAX] if kind == "finding" else (body or "").strip()[:BODY_MAX],
            citations=[_clean_citation(c) for c in citations if isinstance(c, dict)],
            source_session_id=source_session_id, created_by=user_id,
        ))
        self._store.touch(tenant_id, project_id)
        self._store.log(tenant_id, project_id, user_id, f"{kind}_added", {"title": title})
        return item

    @staticmethod
    def _author_or_admin(item: ProjectItem, user_id: str, roles) -> None:
        if item.created_by != user_id and "admin" not in roles:
            raise Forbidden("Only the person who wrote this, or an admin, can change it.")

    def update_item(self, tenant_id: str, user_id: str, roles, project_id: str, item_id: str, *,
                    title: str | None = None, body: str | None = None) -> ProjectItem:
        self._writable(tenant_id, project_id)
        item = self._store.get_item(tenant_id, project_id, item_id)
        self._author_or_admin(item, user_id, roles)
        fields = {}
        if title is not None:
            fields["title"] = _clean(title, TITLE_MAX)
            if not fields["title"]:
                raise ProjectRejected("Give it a title.")
        if body is not None:
            fields["body"] = body.strip()[:BODY_MAX]
        return self._store.update_item(tenant_id, project_id, item_id, **fields)

    def delete_item(self, tenant_id: str, user_id: str, roles, project_id: str, item_id: str) -> None:
        self._writable(tenant_id, project_id)
        item = self._store.get_item(tenant_id, project_id, item_id)
        self._author_or_admin(item, user_id, roles)
        self._store.delete_item(tenant_id, project_id, item_id)

    def items(self, tenant_id: str, project_id: str) -> list[ProjectItem]:
        return self._store.list_items(tenant_id, project_id)

    # ── Insights ─────────────────────────────────────────────────────────
    def insights(self, tenant_id: str, project_id: str) -> dict[str, list[dict]]:
        """Decisions, risks, action items, owners and deadlines found in the project's documents."""
        docs = {d.id: d.title for d in self.documents(tenant_id, project_id)}
        out: dict[str, list[dict]] = {label: [] for label, _ in reports.INSIGHT_SECTIONS}
        if not docs or self._entities is None:
            return out
        seen: set[tuple[str, str]] = set()
        for record in self._entities.records_for_documents(tenant_id, list(docs)):
            if record.label not in out or record.document_id not in docs:
                continue
            key = (record.label, record.value.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            out[record.label].append({
                "value": record.value.strip(), "document_id": record.document_id,
                "document_title": docs[record.document_id], "confidence": record.confidence,
            })
        return out

    # ── Search ───────────────────────────────────────────────────────────
    def search(self, tenant_id: str, project_id: str, query: str) -> dict[str, list[dict]]:
        q = query.strip().lower()
        if not q:
            return {"documents": [], "items": [], "reports": []}
        docs = [
            {"document_id": d.id, "title": d.title}
            for d in self.documents(tenant_id, project_id)
            if q in d.title.lower() or q in (d.file_name or "").lower()
        ]
        items = [
            {"id": i.id, "kind": i.kind, "title": i.title}
            for i in self._store.list_items(tenant_id, project_id)
            if q in i.title.lower() or q in i.body.lower()
        ]
        found_reports = [
            {"id": r.id, "title": r.title}
            for r in self._store.list_reports(tenant_id, project_id) if q in r.title.lower()
        ]
        return {"documents": docs, "items": items, "reports": found_reports}

    # ── Reports ──────────────────────────────────────────────────────────
    def report_for_project(self, tenant_id: str, user_id: str, project_id: str) -> ProjectReport:
        project = self._store.get_project(tenant_id, project_id)
        all_items = self._store.list_items(tenant_id, project_id)
        findings = sorted((i for i in all_items if i.kind == "finding"), key=lambda i: i.created_at)
        notes = sorted((i for i in all_items if i.kind == "note"), key=lambda i: i.created_at)
        titles = [d.title for d in self.documents(tenant_id, project_id)]
        now = time.time()
        markdown = reports.project_report(project, findings, notes, self.insights(tenant_id, project_id),
                                          titles, now=now)
        report = self._store.add_report(ProjectReport(
            id=new_id(), project_id=project_id, tenant_id=tenant_id, source="project", source_session_id=None,
            title=f"{project.name} report", markdown=markdown, created_by=user_id,
        ))
        self._store.log(tenant_id, project_id, user_id, "report_created", {"title": report.title})
        return report

    def report_for_session(self, tenant_id: str, user_id: str, session_id: str) -> ProjectReport:
        detail = self._sessions.get(tenant_id, user_id, session_id)  # SessionNotFound for others
        project_id = detail.session.project_id
        if project_id is not None:
            try:
                self._store.get_project(tenant_id, project_id)
            except ProjectNotFound:
                project_id = None
        if project_id is not None and self._store.get_project(tenant_id, project_id).archived_at is not None:
            raise InvalidState("This session's project is archived. Restore it to add reports.")
        markdown = reports.session_report(detail.session.title, detail.messages, now=time.time())
        report = self._store.add_report(ProjectReport(
            id=new_id(), project_id=project_id, tenant_id=tenant_id, source="session",
            source_session_id=session_id, title=detail.session.title, markdown=markdown, created_by=user_id,
        ))
        if project_id is not None:
            self._store.log(tenant_id, project_id, user_id, "report_created", {"title": report.title})
        return report

    def reports(self, tenant_id: str, project_id: str) -> list[ProjectReport]:
        self._store.get_project(tenant_id, project_id)
        return self._store.list_reports(tenant_id, project_id)

    def my_reports(self, tenant_id: str, user_id: str) -> list[ProjectReport]:
        return self._store.list_reports_by(tenant_id, user_id)

    def get_report(self, tenant_id: str, user_id: str, report_id: str) -> ProjectReport:
        report = self._store.get_report(tenant_id, report_id)
        # A report of a private session (no project) is visible only to its author.
        if report.project_id is None and report.created_by != user_id:
            raise ProjectNotFound(report_id)
        return report

    # ── Activity ─────────────────────────────────────────────────────────
    def activity(self, tenant_id: str, project_id: str | None = None, limit: int = 50):
        if project_id is not None:
            self._store.get_project(tenant_id, project_id)
        return self._store.activity(tenant_id, project_id, limit)
