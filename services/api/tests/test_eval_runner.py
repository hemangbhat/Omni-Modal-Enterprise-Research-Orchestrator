"""The retrieval evaluation runs hermetically and its regression gate works."""

from __future__ import annotations

import copy
import unittest

import _path  # noqa: F401

from omni_modal.benchmark.eval import check, load_corpus, run


class EvalRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus()
        cls.report = run(["hashing"], cls.corpus)

    def test_corpus_labels_are_consistent(self) -> None:
        ids = {d["id"] for d in self.corpus["documents"]}
        for q in self.corpus["questions"]:
            self.assertTrue(set(q["relevant"]) <= ids, q["id"])
            texts = " ".join(d["text"] for d in self.corpus["documents"] if d["id"] in q["relevant"])
            self.assertIn(q["fact"].lower(), texts.lower(), q["id"])

    def test_hybrid_beats_vector_on_hashing(self) -> None:
        by_mode = {r["mode"]: r for r in self.report["results"]}
        self.assertGreater(by_mode["hybrid"]["mrr"], by_mode["vector"]["mrr"])
        self.assertGreater(by_mode["hybrid"]["recall_at_1"], by_mode["vector"]["recall_at_1"])

    def test_check_flags_regressions_only(self) -> None:
        self.assertEqual(check(self.report, self.report, 0.02), [])
        better = copy.deepcopy(self.report)
        for r in better["results"]:
            r["mrr"] += 0.1
        self.assertEqual(len(check(self.report, better, 0.02)), 2)
