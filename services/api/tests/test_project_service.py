"""ProjectService: projects, document links, findings, notes, reports, insights, search."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from omni_modal.data_access import EntityRecord
from omni_modal.documents.service import Forbidden, InvalidState
from omni_modal.documents.store import DocumentNotFound, DocumentRecord, InMemoryDocumentStore
from omni_modal.projects.reports import renumber_citations
from omni_modal.projects.service import ProjectRejected, ProjectService
from omni_modal.projects.store import InMemoryProjectStore, ProjectNotFound
from omni_modal.research.sessions import InMemorySessionStore, ResearchSessionService, SessionNotFound


class _Docs:
    """The slice of DocumentService that projects use."""

    def __init__(self) -> None:
        self.store = InMemoryDocumentStore()

    def add(self, tenant: str, doc_id: str, title: str) -> None:
        self.store.create_document(DocumentRecord(
            id=doc_id, tenant_id=tenant, uploaded_by="u1", title=title, file_name=f"{title}.pdf",
            source_kind="pdf", content_type="application/pdf", size_bytes=10, content_hash=doc_id.ljust(64, "0"),
            storage_key=f"k/{doc_id}", status="ready",
        ))

    def get(self, tenant_id: str, document_id: str):
        return self.store.get_document(tenant_id, document_id), None


class _Entities:
    def __init__(self) -> None:
        self.records: list[EntityRecord] = []

    def records_for_documents(self, tenant_id, document_ids):
        return [r for r in self.records if r.tenant_id == tenant_id and r.document_id in document_ids]


CITE_A = {"document_id": "d1", "chunk_id": "c1", "title": "Board Minutes", "page_number": 2,
          "start_ms": None, "end_ms": None, "segment_index": None, "snippet": "Revenue grew 18%.",
          "scope": "internal", "cited": True, "source_type": "pdf", "chunk_index": 0}
CITE_B = {**CITE_A, "chunk_id": "c2", "page_number": 5, "snippet": "Headcount rose to 145."}


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.docs = _Docs()
        self.docs.add("t1", "d1", "Board Minutes")
        self.docs.add("t1", "d2", "Hiring Plan")
        self.docs.add("t2", "x1", "Other Tenant Doc")
        self.sessions = ResearchSessionService(InMemorySessionStore())
        self.entities = _Entities()
        self.svc = ProjectService(store=InMemoryProjectStore(), documents=self.docs,
                                  sessions=self.sessions, entities=self.entities)
        self.p = self.svc.create("t1", "u1", name="  Q3 review  ", description="Board prep")

    # ── Basics ───────────────────────────────────────────────────────────
    def test_create_list_and_rename(self) -> None:
        self.assertEqual(self.p.name, "Q3 review")
        listed = self.svc.list("t1")
        self.assertEqual([entry["project"].id for entry in listed], [self.p.id])
        self.assertEqual(listed[0]["counts"], {"documents": 0, "findings": 0, "notes": 0, "reports": 0})
        self.assertEqual(self.svc.update("t1", "u2", self.p.id, name="Q3 board review").name, "Q3 board review")
        with self.assertRaises(ProjectRejected):
            self.svc.create("t1", "u1", name="   ")

    def test_other_tenant_cannot_see_or_change_the_project(self) -> None:
        for call in (
            lambda: self.svc.get("t2", self.p.id),
            lambda: self.svc.update("t2", "u1", self.p.id, name="x"),
            lambda: self.svc.add_document("t2", "u1", self.p.id, "d1"),
            lambda: self.svc.add_item("t2", "u1", self.p.id, kind="note", title="x"),
            lambda: self.svc.report_for_project("t2", "u1", self.p.id),
        ):
            with self.assertRaises(ProjectNotFound):
                call()
        self.assertEqual(self.svc.list("t2"), [])

    def test_only_documents_of_the_same_tenant_can_be_linked(self) -> None:
        self.svc.add_document("t1", "u1", self.p.id, "d1")
        with self.assertRaises(DocumentNotFound):
            self.svc.add_document("t1", "u1", self.p.id, "x1")
        self.assertEqual([d.id for d in self.svc.documents("t1", self.p.id)], ["d1"])
        self.assertEqual(self.svc.scope("t1", self.p.id), ("d1",))

    def test_archive_is_for_creator_or_admin_and_makes_the_project_read_only(self) -> None:
        with self.assertRaises(Forbidden):
            self.svc.archive("t1", "u2", ("researcher",), self.p.id)
        self.svc.archive("t1", "u2", ("admin",), self.p.id)
        with self.assertRaises(InvalidState):
            self.svc.add_item("t1", "u1", self.p.id, kind="note", title="late note")
        self.assertEqual(self.svc.list("t1"), [])
        self.assertEqual(len(self.svc.list("t1", include_archived=True)), 1)
        self.svc.restore("t1", "u1", ("researcher",), self.p.id)
        self.svc.add_item("t1", "u1", self.p.id, kind="note", title="back again")

    # ── Sessions ─────────────────────────────────────────────────────────
    def test_sessions_link_only_to_own_tenant_projects(self) -> None:
        s = self.sessions.create("t1", "u1", title="Revenue")
        self.svc.link_session("t1", "u1", self.p.id, s.id)
        self.assertEqual([x.id for x in self.svc.sessions("t1", "u1", self.p.id)], [s.id])
        self.assertEqual(self.svc.sessions("t1", "u2", self.p.id), [])  # sessions stay private
        other = self.svc.create("t2", "u9", name="Theirs")
        with self.assertRaises(ProjectNotFound):
            self.svc.link_session("t1", "u1", other.id, s.id)
        with self.assertRaises(SessionNotFound):
            self.svc.link_session("t1", "u2", self.p.id, s.id)

    # ── Findings and notes ───────────────────────────────────────────────
    def test_finding_keeps_answer_text_and_citations_only(self) -> None:
        item = self.svc.add_item(
            "t1", "u1", self.p.id, kind="finding", title="How much did revenue grow?",
            body="## Answer\n\nRevenue grew 18% [1].\n\n## Sources\n\n1. [1] Board Minutes, p. 2",
            citations=[{**CITE_A, "extra": "dropped"}],
        )
        self.assertEqual(item.body, "Revenue grew 18% [1].")
        self.assertNotIn("extra", item.citations[0])
        self.assertEqual(item.citations[0]["page_number"], 2)

    def test_only_author_or_admin_edits_items(self) -> None:
        item = self.svc.add_item("t1", "u1", self.p.id, kind="note", title="Mine", body="text")
        with self.assertRaises(Forbidden):
            self.svc.update_item("t1", "u2", ("researcher",), self.p.id, item.id, body="changed")
        self.svc.update_item("t1", "u2", ("admin",), self.p.id, item.id, body="changed")
        with self.assertRaises(Forbidden):
            self.svc.delete_item("t1", "u2", ("researcher",), self.p.id, item.id)
        self.svc.delete_item("t1", "u1", ("researcher",), self.p.id, item.id)

    def test_rejects_unknown_kind_and_too_many_citations(self) -> None:
        with self.assertRaises(ProjectRejected):
            self.svc.add_item("t1", "u1", self.p.id, kind="task", title="x")
        with self.assertRaises(ProjectRejected):
            self.svc.add_item("t1", "u1", self.p.id, kind="finding", title="x", citations=[CITE_A] * 51)

    # ── Reports ──────────────────────────────────────────────────────────
    def test_project_report_merges_citations_into_one_numbered_source_list(self) -> None:
        self.svc.add_document("t1", "u1", self.p.id, "d1")
        self.svc.add_item("t1", "u1", self.p.id, kind="finding", title="Revenue",
                          body="Revenue grew 18% [1].", citations=[CITE_A])
        self.svc.add_item("t1", "u1", self.p.id, kind="finding", title="People",
                          body="Headcount rose [1] while revenue grew [2].", citations=[CITE_B, CITE_A])
        self.svc.add_item("t1", "u1", self.p.id, kind="note", title="Follow up", body="Ask finance.")
        self.entities.records = [
            EntityRecord("e1", "d1", "t1", "decision", "approved a budget of 2.4 million", 0.8),
            EntityRecord("e2", "d1", "t1", "risk", "security hiring", 0.7),
            EntityRecord("e3", "x1", "t2", "risk", "other tenant risk", 0.9),
        ]
        report = self.svc.report_for_project("t1", "u1", self.p.id)
        md = report.markdown
        self.assertIn("# Q3 review", md)
        self.assertIn("Revenue grew 18% [1].", md)
        # The second finding's [1] is CITE_B (new source 2); its [2] is CITE_A (source 1).
        self.assertIn("Headcount rose [2] while revenue grew [1].", md)
        sources = md.split("## Sources", 1)[1]
        self.assertIn('1. Board Minutes, page 2: "Revenue grew 18%."', sources)
        self.assertIn('2. Board Minutes, page 5: "Headcount rose to 145."', sources)
        self.assertIn("approved a budget of 2.4 million", md)
        self.assertNotIn("other tenant risk", md)
        self.assertIn("Ask finance.", md)
        self.assertEqual(self.svc.reports("t1", self.p.id)[0].id, report.id)
        self.assertEqual([a.action for a in self.svc.activity("t1", self.p.id)][0], "report_created")

    def test_session_report_lists_each_question_with_its_sources(self) -> None:
        s = self.sessions.create("t1", "u1", title="Revenue questions", project_id=self.p.id)
        self.sessions.append_turn("t1", "u1", s.id, question="How much did revenue grow?",
                                  answer="## Answer\n\nIt grew 18% [1].\n\n## Sources\n\n1. x",
                                  status="answered", citations=[CITE_A])
        report = self.svc.report_for_session("t1", "u1", s.id)
        self.assertEqual(report.project_id, self.p.id)
        self.assertIn("## 1. How much did revenue grow?", report.markdown)
        self.assertIn("It grew 18% [1].", report.markdown)
        self.assertNotIn("## Answer", report.markdown)
        with self.assertRaises(SessionNotFound):
            self.svc.report_for_session("t1", "u2", s.id)

    def test_private_session_report_is_visible_only_to_its_author(self) -> None:
        s = self.sessions.create("t1", "u1", title="Private")
        self.sessions.append_turn("t1", "u1", s.id, question="q", answer="a", status="answered")
        report = self.svc.report_for_session("t1", "u1", s.id)
        self.assertIsNone(report.project_id)
        self.assertEqual(self.svc.get_report("t1", "u1", report.id).id, report.id)
        with self.assertRaises(ProjectNotFound):
            self.svc.get_report("t1", "u2", report.id)

    def test_renumbering_drops_markers_without_a_citation(self) -> None:
        registry: list[dict] = []
        self.assertEqual(renumber_citations("A [1] B [3].", [CITE_A], registry), "A [1] B.")

    # ── Insights, search, activity ───────────────────────────────────────
    def test_insights_group_entities_of_the_projects_documents(self) -> None:
        self.svc.add_document("t1", "u1", self.p.id, "d1")
        self.entities.records = [
            EntityRecord("e1", "d1", "t1", "decision", "approved the budget", 0.8),
            EntityRecord("e2", "d1", "t1", "action_item", "Priya to present hiring plan", 0.6),
            EntityRecord("e3", "d2", "t1", "risk", "not in project", 0.6),
            EntityRecord("e4", "d1", "t1", "kpi", "18 percent", 0.6),
        ]
        insights = self.svc.insights("t1", self.p.id)
        self.assertEqual([x["value"] for x in insights["decision"]], ["approved the budget"])
        self.assertEqual(insights["action_item"][0]["document_title"], "Board Minutes")
        self.assertEqual(insights["risk"], [])
        self.assertNotIn("kpi", insights)

    def test_search_within_project(self) -> None:
        self.svc.add_document("t1", "u1", self.p.id, "d2")
        self.svc.add_item("t1", "u1", self.p.id, kind="note", title="Hiring follow-up", body="Call Priya")
        hits = self.svc.search("t1", self.p.id, "hiring")
        self.assertEqual([d["title"] for d in hits["documents"]], ["Hiring Plan"])
        self.assertEqual([i["title"] for i in hits["items"]], ["Hiring follow-up"])
        self.assertEqual(self.svc.search("t1", self.p.id, "priya")["items"][0]["title"], "Hiring follow-up")

    def test_activity_records_what_happened(self) -> None:
        self.svc.add_document("t1", "u1", self.p.id, "d1")
        self.svc.remove_document("t1", "u1", self.p.id, "d1")
        actions = [a.action for a in self.svc.activity("t1", self.p.id)]
        self.assertEqual(actions, ["document_removed", "document_added", "project_created"])


if __name__ == "__main__":
    unittest.main()
