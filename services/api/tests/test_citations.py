"""Citation post-processing: invalid markers, cited flags, snippets, no-evidence."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from omni_modal.qa.citations import NO_EVIDENCE_SENTENCE, finalize_answer, strip_invalid_markers
from omni_modal.qa.models import QueryRequest, RetrievedChunk


def _chunk(i: int, content: str, **metadata) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{i}", document_id="d1", title="Q3 Report", source_type="pdf",
        chunk_index=i, content=content, similarity=0.9 - i / 10, metadata=metadata,
    )


def _request(question: str = "How much did revenue grow?") -> QueryRequest:
    return QueryRequest(tenant_id="t1", user_id="u1", question=question)


class StripMarkerTests(unittest.TestCase):
    def test_out_of_range_markers_are_removed(self) -> None:
        text, cited = strip_invalid_markers("Revenue grew [1]. Costs fell [7]. Staff [0].", 2)
        self.assertEqual(text, "Revenue grew [1]. Costs fell. Staff.")
        self.assertEqual(cited, {1})

    def test_grouped_markers_keep_only_valid_numbers(self) -> None:
        text, cited = strip_invalid_markers("Both [1, 2, 9] agree.", 2)
        self.assertEqual(text, "Both [1, 2] agree.")
        self.assertEqual(cited, {1, 2})

    def test_full_width_brackets_are_normalised(self) -> None:
        # Some models (gpt-oss) cite as 【1】.
        self.assertEqual(strip_invalid_markers("Revenue grew【1】 and fell【8】.", 2), ("Revenue grew [1] and fell.", {1}))

    def test_full_width_non_numeric_markers_are_removed(self) -> None:
        self.assertEqual(strip_invalid_markers("You have 1 document 【list_documents】.", 2), ("You have 1 document.", set()))

    def test_removing_a_marker_keeps_line_breaks(self) -> None:
        text, _ = strip_invalid_markers("First point.\n\n[9] Second point [1].", 2)
        self.assertEqual(text, "First point.\n\n Second point [1].")

    def test_brackets_that_are_not_citations_are_left_alone(self) -> None:
        for sample in ("Use items[0] here.", "Founded in [2024].", "See matrix[1][2]."):
            self.assertEqual(strip_invalid_markers(sample, 1), (sample, set()))

    def test_text_without_markers_is_unchanged(self) -> None:
        self.assertEqual(strip_invalid_markers("Plain text.", 3), ("Plain text.", set()))


class FinalizeAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            _chunk(0, "Intro text. Revenue grew 18% on enterprise subscriptions.", pageNumber=4),
            _chunk(1, "Headcount rose to 145.", startMs=65_000, endMs=93_000),
        ]

    def test_marks_cited_passages_and_keeps_numbering(self) -> None:
        resp = finalize_answer(_request(), self.chunks, "Revenue grew 18% [1].")
        self.assertEqual(resp.status, "answered")
        self.assertEqual([c.cited for c in resp.citations], [True, False])
        self.assertEqual(len(resp.citations), 2)

    def test_snippet_is_the_most_relevant_sentence(self) -> None:
        resp = finalize_answer(_request(), self.chunks, "Revenue grew 18% [1].")
        self.assertEqual(resp.citations[0].snippet, "Revenue grew 18% on enterprise subscriptions.")
        self.assertEqual(resp.citations[0].scope, "internal")
        self.assertEqual(resp.citations[0].page_number, 4)

    def test_snippet_matches_the_claim_that_cites_it(self) -> None:
        chunks = [_chunk(0, "People and hiring. Headcount rose to 145 employees. Priya Shah owns the hiring plan.")]
        resp = finalize_answer(
            _request("Summarize the key decisions"), chunks, "Priya Shah must deliver a hiring plan [1]."
        )
        self.assertEqual(resp.citations[0].snippet, "Priya Shah owns the hiring plan.")

    def test_snippet_ignores_filler_words(self) -> None:
        from omni_modal.qa.citations import best_excerpt

        content = "People and hiring. Headcount rose to 145 employees by the end of Q3."
        self.assertEqual(
            best_excerpt(content, "How much did revenue grow? and what about headcount?"),
            "Headcount rose to 145 employees by the end of Q3.",
        )

    def test_sources_list_only_cited_passages_with_location(self) -> None:
        resp = finalize_answer(_request(), self.chunks, "Staff grew [2].")
        sources = resp.answer_markdown.split("## Sources", 1)[1]
        self.assertIn("[2] Q3 Report, 1:05–1:33", sources)
        self.assertNotIn("[1]", sources)

    def test_invalid_marker_removed_from_final_answer(self) -> None:
        resp = finalize_answer(_request(), self.chunks, "Revenue grew [1] and margin rose [5].")
        self.assertNotIn("[5]", resp.answer_markdown)

    def test_no_evidence_reply_becomes_no_data_without_citations(self) -> None:
        resp = finalize_answer(_request(), self.chunks, NO_EVIDENCE_SENTENCE)
        self.assertEqual(resp.status, "no_data")
        self.assertEqual(resp.citations, [])
        self.assertNotIn("## Sources", resp.answer_markdown)
        self.assertIn("not contain enough information", resp.answer_markdown)

    def test_uncited_answer_lists_all_passages(self) -> None:
        resp = finalize_answer(_request(), self.chunks, "Revenue grew.")
        self.assertEqual(resp.status, "answered")
        self.assertIn("[1]", resp.answer_markdown.split("## Sources", 1)[1])
        self.assertIn("[2]", resp.answer_markdown.split("## Sources", 1)[1])


if __name__ == "__main__":
    unittest.main()
