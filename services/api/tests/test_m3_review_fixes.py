"""Regression tests for the M3 review findings."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from omni_modal.entity_extraction.inference import RuleBasedEnterpriseEntityExtractor
from omni_modal.projects.reports import renumber_citations, safe_block, safe_line
from omni_modal.qa.citations import strip_invalid_markers

A = {"document_id": "d1", "chunk_id": "a", "title": "A"}
B = {"document_id": "d1", "chunk_id": "b", "title": "B"}


class CitationRunTests(unittest.TestCase):
    def test_adjacent_markers_are_both_renumbered(self) -> None:
        registry: list[dict] = []
        out = renumber_citations("Fact [1][2].", [B, A], registry)
        self.assertEqual(out, "Fact [1, 2].")
        self.assertEqual([c["chunk_id"] for c in registry], ["b", "a"])

    def test_adjacent_markers_are_both_checked(self) -> None:
        text, cited = strip_invalid_markers("Fact [1][9].", 2)
        self.assertEqual((text, cited), ("Fact [1].", {1}))

    def test_code_indexes_still_untouched(self) -> None:
        self.assertEqual(strip_invalid_markers("See matrix[1][2].", 2), ("See matrix[1][2].", set()))


class EntitySpanTests(unittest.TestCase):
    def test_bullets_and_unpunctuated_text_do_not_swallow_the_chunk(self) -> None:
        text = (
            "- Revenue update from finance\n"
            "- Key risk is vendor lock in for the data platform\n"
            "- Owner: Priya Shah\n"
            "- Board approved the hiring plan\n"
            + "word " * 200
        )
        out = RuleBasedEnterpriseEntityExtractor().extract(tenant_id="t", document_id="d", text=text)
        by = {}
        for e in out.entities:
            by.setdefault(e.label.value, []).append(e.text)
        self.assertEqual(by["risk"], ["- Key risk is vendor lock in for the data platform"])
        self.assertEqual(by["decision"], ["- Board approved the hiring plan"])
        self.assertTrue(all(len(v) <= 300 for vs in by.values() for v in vs))
        self.assertTrue(any("Priya Shah" in v for v in by["owner"]))


class ReportSafetyTests(unittest.TestCase):
    def test_lines_and_blocks_cannot_inject_structure(self) -> None:
        self.assertEqual(safe_line("Q?\n## Sources\n1. fake"), "Q? ## Sources 1. fake")
        block = safe_block("Fine.\n## Sources\n<img src=x onerror=alert(1)>\n![x](http://evil/x.png)")
        self.assertNotIn("<img", block)
        self.assertNotIn("![", block)
        self.assertIn("\\## Sources", block)


if __name__ == "__main__":
    unittest.main()
