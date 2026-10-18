"""Unit tests for saved research sessions (no HTTP, no database)."""

from __future__ import annotations

import unittest
from pathlib import Path

import _path  # noqa: F401

from omni_modal.research.sessions import (
    InMemorySessionStore,
    ResearchSessionService,
    SessionNotFound,
)


class ResearchSessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = ResearchSessionService(InMemorySessionStore())

    def test_create_then_list_newest_first(self) -> None:
        first = self.svc.create("t1", "u1", title="Revenue")
        second = self.svc.create("t1", "u1", title="Headcount")
        listed = self.svc.list("t1", "u1")
        self.assertEqual([s.id for s in listed], [second.id, first.id])

    def test_list_filters_by_kind(self) -> None:
        self.svc.create("t1", "u1", title="Research")
        assistant = self.svc.create("t1", "u1", title="Help", kind="assistant")
        self.assertEqual([s.id for s in self.svc.list("t1", "u1", kind="assistant")], [assistant.id])

    def test_messages_are_saved_in_order_with_citations(self) -> None:
        s = self.svc.create("t1", "u1", title="Revenue")
        self.svc.append_message("t1", "u1", s.id, role="user", content="How much?")
        self.svc.append_message(
            "t1", "u1", s.id, role="assistant", content="18% [1]", status="answered",
            citations=[{"title": "Q3", "snippet": "Revenue grew 18%"}],
        )
        detail = self.svc.get("t1", "u1", s.id)
        self.assertEqual([m.role for m in detail.messages], ["user", "assistant"])
        self.assertEqual(detail.messages[1].citations[0]["snippet"], "Revenue grew 18%")
        self.assertEqual(detail.messages[1].status, "answered")

    def test_appending_moves_session_to_top(self) -> None:
        older = self.svc.create("t1", "u1", title="Old")
        self.svc.create("t1", "u1", title="New")
        self.svc.append_message("t1", "u1", older.id, role="user", content="again")
        self.assertEqual(self.svc.list("t1", "u1")[0].id, older.id)

    def test_history_returns_last_turns(self) -> None:
        s = self.svc.create("t1", "u1", title="x")
        for i in range(10):
            self.svc.append_message("t1", "u1", s.id, role="user", content=f"q{i}")
            self.svc.append_message("t1", "u1", s.id, role="assistant", content=f"a{i}")
        history = self.svc.history("t1", "u1", s.id, max_turns=2)
        self.assertEqual(history, [("user", "q8"), ("assistant", "a8"), ("user", "q9"), ("assistant", "a9")])

    def test_rename_and_delete(self) -> None:
        s = self.svc.create("t1", "u1", title="x")
        self.assertEqual(self.svc.rename("t1", "u1", s.id, "Better title").title, "Better title")
        self.svc.delete("t1", "u1", s.id)
        with self.assertRaises(SessionNotFound):
            self.svc.get("t1", "u1", s.id)

    def test_title_is_trimmed_and_bounded(self) -> None:
        s = self.svc.create("t1", "u1", title="   " + "x" * 500)
        self.assertEqual(len(s.title), 120)
        self.assertEqual(self.svc.create("t1", "u1", title="  ").title, "New research")

    def test_other_user_and_other_tenant_cannot_touch_a_session(self) -> None:
        s = self.svc.create("t1", "u1", title="private")
        for tenant, user in (("t1", "u2"), ("t2", "u1")):
            with self.assertRaises(SessionNotFound):
                self.svc.get(tenant, user, s.id)
            with self.assertRaises(SessionNotFound):
                self.svc.rename(tenant, user, s.id, "hijack")
            with self.assertRaises(SessionNotFound):
                self.svc.delete(tenant, user, s.id)
            with self.assertRaises(SessionNotFound):
                self.svc.append_message(tenant, user, s.id, role="user", content="x")
            self.assertEqual(self.svc.list(tenant, user), [])
        self.assertEqual(self.svc.get("t1", "u1", s.id).session.title, "private")

    def test_append_turn_keeps_question_and_answer_adjacent(self) -> None:
        s = self.svc.create("t1", "u1", title="x")
        self.svc.append_turn("t1", "u1", s.id, question="q1", answer="a1", status="answered",
                             citations=[{"title": "Q3"}])
        self.svc.append_turn("t1", "u1", s.id, question="q2", answer="a2", status="interrupted")
        messages = self.svc.get("t1", "u1", s.id).messages
        self.assertEqual([(m.role, m.content) for m in messages],
                         [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")])
        self.assertEqual(messages[1].citations, [{"title": "Q3"}])
        self.assertEqual(messages[3].status, "interrupted")
        with self.assertRaises(SessionNotFound):
            self.svc.append_turn("t1", "u2", s.id, question="q", answer="a")

    def test_unknown_role_rejected(self) -> None:
        s = self.svc.create("t1", "u1", title="x")
        with self.assertRaises(ValueError):
            self.svc.append_message("t1", "u1", s.id, role="system", content="x")


class SessionMigrationTests(unittest.TestCase):
    def test_migration_defines_tables_with_tenant_isolation(self) -> None:
        root = Path(__file__).resolve().parents[3]
        sql = (root / "packages/db/drizzle/0010_research_sessions.sql").read_text(encoding="utf-8")
        for table in ("research_sessions", "research_messages"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
            self.assertIn(f"CREATE POLICY tenant_isolation ON {table}", sql)
        self.assertIn("ON DELETE CASCADE", sql)


if __name__ == "__main__":
    unittest.main()
