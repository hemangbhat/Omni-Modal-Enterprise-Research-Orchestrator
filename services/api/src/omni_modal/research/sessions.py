"""Saved research and assistant conversations.

A session belongs to one user in one tenant; every read and write is filtered
by both, so another user's session id behaves exactly like an unknown one
(``SessionNotFound``). Assistant turns keep the citations they were answered
with.

Backed by the Postgres ``research_sessions`` / ``research_messages`` tables
(migration 0010) when ``DATABASE_URL`` is set, so conversations survive a
restart, and by a thread-safe in-memory store otherwise.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

SessionKind = Literal["research", "assistant"]
MessageRole = Literal["user", "assistant"]

TITLE_MAX = 120
DEFAULT_TITLE = "New research"
_KINDS = ("research", "assistant")
# (role, content, status, citations) for a message about to be stored.
_NewMessage = tuple[str, str, "str | None", list]
_ROLES = ("user", "assistant")


class SessionNotFound(Exception):
    pass


@dataclass(frozen=True)
class SessionSummary:
    id: str
    kind: str
    title: str
    created_at: float
    updated_at: float
    # The project whose documents this session searches, if any.
    project_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SessionMessage:
    id: str
    role: str
    content: str
    status: str | None
    citations: list[dict]
    created_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SessionDetail:
    session: SessionSummary
    messages: list[SessionMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**self.session.to_dict(), "messages": [m.to_dict() for m in self.messages]}


def clean_title(title: str | None) -> str:
    text = " ".join((title or "").split())
    return text[:TITLE_MAX] if text else DEFAULT_TITLE


class InMemorySessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # id -> {"tenant_id", "user_id", "kind", "title", "created_at", "updated_at", "messages": [...]}
        self._rows: dict[str, dict] = {}
        self._clock = 0.0

    def _now(self) -> float:
        # Strictly increasing, so "newest first" is stable within one clock tick.
        self._clock = max(time.time(), self._clock + 1e-6)
        return self._clock

    def _owned(self, tenant_id: str, user_id: str, session_id: str) -> dict:
        row = self._rows.get(session_id)
        if row is None or row["tenant_id"] != tenant_id or row["user_id"] != user_id:
            raise SessionNotFound(session_id)
        return row

    @staticmethod
    def _summary(session_id: str, row: dict) -> SessionSummary:
        return SessionSummary(session_id, row["kind"], row["title"], row["created_at"], row["updated_at"],
                              row.get("project_id"))

    def create(self, tenant_id: str, user_id: str, kind: str, title: str,
               project_id: str | None = None) -> SessionSummary:
        with self._lock:
            now = self._now()
            session_id = uuid.uuid4().hex
            self._rows[session_id] = {
                "tenant_id": tenant_id, "user_id": user_id, "kind": kind, "title": title,
                "created_at": now, "updated_at": now, "messages": [], "project_id": project_id,
            }
            return self._summary(session_id, self._rows[session_id])

    def list(self, tenant_id: str, user_id: str, kind: str | None, limit: int,
             project_id: str | None = None) -> list[SessionSummary]:
        with self._lock:
            rows = [
                self._summary(sid, row) for sid, row in self._rows.items()
                if row["tenant_id"] == tenant_id and row["user_id"] == user_id
                and (kind is None or row["kind"] == kind)
                and (project_id is None or row.get("project_id") == project_id)
            ]
        rows.sort(key=lambda s: s.updated_at, reverse=True)
        return rows[:limit]

    def get(self, tenant_id: str, user_id: str, session_id: str) -> SessionDetail:
        with self._lock:
            row = self._owned(tenant_id, user_id, session_id)
            return SessionDetail(self._summary(session_id, row), list(row["messages"]))

    def rename(self, tenant_id: str, user_id: str, session_id: str, title: str) -> SessionSummary:
        with self._lock:
            row = self._owned(tenant_id, user_id, session_id)
            row["title"] = title
            row["updated_at"] = self._now()
            return self._summary(session_id, row)

    def set_project(self, tenant_id: str, user_id: str, session_id: str, project_id: str | None) -> SessionSummary:
        with self._lock:
            row = self._owned(tenant_id, user_id, session_id)
            row["project_id"] = project_id
            row["updated_at"] = self._now()
            return self._summary(session_id, row)

    def clear_project(self, tenant_id: str, project_id: str) -> None:
        """A project removed: its sessions stay, unlinked (ON DELETE SET NULL in Postgres)."""
        with self._lock:
            for row in self._rows.values():
                if row["tenant_id"] == tenant_id and row.get("project_id") == project_id:
                    row["project_id"] = None

    def delete(self, tenant_id: str, user_id: str, session_id: str) -> None:
        with self._lock:
            self._owned(tenant_id, user_id, session_id)
            del self._rows[session_id]

    def append(
        self, tenant_id: str, user_id: str, session_id: str, messages: list[_NewMessage],
    ) -> list[SessionMessage]:
        with self._lock:
            row = self._owned(tenant_id, user_id, session_id)
            saved = [
                SessionMessage(uuid.uuid4().hex, role, content, status, list(citations), self._now())
                for role, content, status, citations in messages
            ]
            row["messages"].extend(saved)
            row["updated_at"] = saved[-1].created_at if saved else row["updated_at"]
            return saved


class PostgresSessionStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    @staticmethod
    def _summary(row) -> SessionSummary:
        return SessionSummary(row[0], row[1], row[2], row[3].timestamp(), row[4].timestamp(), row[5])

    def create(self, tenant_id: str, user_id: str, kind: str, title: str,
               project_id: str | None = None) -> SessionSummary:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO research_sessions (id, tenant_id, user_id, kind, title, project_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, kind, title, created_at, updated_at, project_id""",
                (uuid.uuid4().hex, tenant_id, user_id, kind, title, project_id),
            )
            return self._summary(cur.fetchone())

    def list(self, tenant_id: str, user_id: str, kind: str | None, limit: int,
             project_id: str | None = None) -> list[SessionSummary]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, kind, title, created_at, updated_at, project_id FROM research_sessions
                   WHERE tenant_id = %s AND user_id = %s AND (%s::text IS NULL OR kind = %s)
                     AND (%s::text IS NULL OR project_id = %s)
                   ORDER BY updated_at DESC LIMIT %s""",
                (tenant_id, user_id, kind, kind, project_id, project_id, limit),
            )
            return [self._summary(r) for r in cur.fetchall()]

    def _owned_summary(self, cur, tenant_id: str, user_id: str, session_id: str) -> SessionSummary:
        cur.execute(
            """SELECT id, kind, title, created_at, updated_at, project_id FROM research_sessions
               WHERE id = %s AND tenant_id = %s AND user_id = %s""",
            (session_id, tenant_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return self._summary(row)

    def get(self, tenant_id: str, user_id: str, session_id: str) -> SessionDetail:
        with self._pool.connection() as conn, conn.cursor() as cur:
            summary = self._owned_summary(cur, tenant_id, user_id, session_id)
            cur.execute(
                """SELECT id, role, content, status, citations, created_at FROM research_messages
                   WHERE session_id = %s AND tenant_id = %s ORDER BY seq""",
                (session_id, tenant_id),
            )
            messages = [
                SessionMessage(r[0], r[1], r[2], r[3], _as_list(r[4]), r[5].timestamp())
                for r in cur.fetchall()
            ]
        return SessionDetail(summary, messages)

    def rename(self, tenant_id: str, user_id: str, session_id: str, title: str) -> SessionSummary:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE research_sessions SET title = %s, updated_at = now()
                   WHERE id = %s AND tenant_id = %s AND user_id = %s
                   RETURNING id, kind, title, created_at, updated_at, project_id""",
                (title, session_id, tenant_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return self._summary(row)

    def set_project(self, tenant_id: str, user_id: str, session_id: str, project_id: str | None) -> SessionSummary:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE research_sessions SET project_id = %s, updated_at = now()
                   WHERE id = %s AND tenant_id = %s AND user_id = %s
                   RETURNING id, kind, title, created_at, updated_at, project_id""",
                (project_id, session_id, tenant_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return self._summary(row)

    def clear_project(self, tenant_id: str, project_id: str) -> None:
        pass  # ON DELETE SET NULL

    def delete(self, tenant_id: str, user_id: str, session_id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM research_sessions WHERE id = %s AND tenant_id = %s AND user_id = %s",
                (session_id, tenant_id, user_id),
            )
            if cur.rowcount == 0:
                raise SessionNotFound(session_id)

    def append(
        self, tenant_id: str, user_id: str, session_id: str, messages: list[_NewMessage],
    ) -> list[SessionMessage]:
        # One connection checkout is one transaction: all rows land together.
        with self._pool.connection() as conn, conn.cursor() as cur:
            # Lock the owner's session row: proves ownership and serialises seq.
            cur.execute(
                """UPDATE research_sessions SET updated_at = now()
                   WHERE id = %s AND tenant_id = %s AND user_id = %s RETURNING id""",
                (session_id, tenant_id, user_id),
            )
            if cur.fetchone() is None:
                raise SessionNotFound(session_id)
            saved = []
            for role, content, status, citations in messages:
                message_id = uuid.uuid4().hex
                cur.execute(
                    """INSERT INTO research_messages (id, session_id, tenant_id, seq, role, content, status, citations)
                       VALUES (%s, %s, %s,
                               (SELECT COALESCE(MAX(seq), 0) + 1 FROM research_messages WHERE session_id = %s),
                               %s, %s, %s, %s::jsonb)
                       RETURNING created_at""",
                    (message_id, session_id, tenant_id, session_id, role, content, status, json.dumps(citations)),
                )
                created_at = cur.fetchone()[0].timestamp()
                saved.append(SessionMessage(message_id, role, content, status, list(citations), created_at))
        return saved


def _as_list(value) -> list[dict]:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value) if isinstance(value, list) else []


class ResearchSessionService:
    def __init__(self, store=None) -> None:
        self._store = store or InMemorySessionStore()

    def create(self, tenant_id: str, user_id: str, *, title: str | None = None, kind: str = "research",
               project_id: str | None = None) -> SessionSummary:
        """``project_id`` must already be checked as the caller's tenant's project."""
        if kind not in _KINDS:
            raise ValueError(f"Unknown session kind: {kind}")
        return self._store.create(tenant_id, user_id, kind, clean_title(title), project_id)

    def list(self, tenant_id: str, user_id: str, *, kind: str | None = None, limit: int = 50,
             project_id: str | None = None) -> list[SessionSummary]:
        return self._store.list(tenant_id, user_id, kind, max(1, min(limit, 200)), project_id)

    def set_project(self, tenant_id: str, user_id: str, session_id: str, project_id: str | None) -> SessionSummary:
        """``project_id`` must already be checked as the caller's tenant's project."""
        return self._store.set_project(tenant_id, user_id, session_id, project_id)

    def get(self, tenant_id: str, user_id: str, session_id: str) -> SessionDetail:
        return self._store.get(tenant_id, user_id, session_id)

    def rename(self, tenant_id: str, user_id: str, session_id: str, title: str) -> SessionSummary:
        return self._store.rename(tenant_id, user_id, session_id, clean_title(title))

    def delete(self, tenant_id: str, user_id: str, session_id: str) -> None:
        self._store.delete(tenant_id, user_id, session_id)

    def append_message(
        self, tenant_id: str, user_id: str, session_id: str, *,
        role: str, content: str, status: str | None = None, citations: list[dict] | None = None,
    ) -> SessionMessage:
        if role not in _ROLES:
            raise ValueError(f"Unknown message role: {role}")
        return self._store.append(tenant_id, user_id, session_id, [(role, content, status, citations or [])])[0]

    def append_turn(
        self, tenant_id: str, user_id: str, session_id: str, *,
        question: str, answer: str, status: str | None = None, citations: list[dict] | None = None,
    ) -> None:
        """Save a question and its answer together, so concurrent turns never interleave."""
        self._store.append(tenant_id, user_id, session_id, [
            ("user", question, None, []),
            ("assistant", answer, status, citations or []),
        ])

    def history(self, tenant_id: str, user_id: str, session_id: str, *, max_turns: int = 4) -> list[tuple[str, str]]:
        """The last ``max_turns`` question/answer pairs, oldest first."""
        messages = self.get(tenant_id, user_id, session_id).messages
        return [(m.role, m.content) for m in messages[-2 * max_turns:]]


_in_memory_store: InMemorySessionStore | None = None
_store_lock = threading.Lock()


def _shared_store() -> InMemorySessionStore:
    global _in_memory_store
    if _in_memory_store is None:
        with _store_lock:
            if _in_memory_store is None:
                _in_memory_store = InMemorySessionStore()
    return _in_memory_store


def select_research_session_service() -> ResearchSessionService:
    """Postgres-backed when configured (sessions survive restarts), else in-process."""
    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return ResearchSessionService(PostgresSessionStore(get_connection_pool()))
        except Exception as exc:  # noqa: BLE001 - fall back to the in-process store
            from omni_modal.observability import observability  # noqa: PLC0415

            observability.capture_message(
                f"DATABASE_URL is set but the research session store could not use Postgres ({exc}); "
                "sessions will NOT survive a restart.",
                operation="research.sessions.select", level="error",
            )
    return ResearchSessionService(_shared_store())
