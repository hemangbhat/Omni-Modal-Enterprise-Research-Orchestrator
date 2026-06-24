"""Tests for SaaS plans and limit enforcement (saas/plans.py)."""
from __future__ import annotations

import unittest

import _path  # noqa: F401
import pytest
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.saas.plans import (
    PLANS,
    DEFAULT_PLAN_ID,
    PlanLimitExceeded,
    assert_within_limit,
    feature_enabled,
    get_plan,
)


class TestPlans(unittest.TestCase):
    def test_known_plans_exist(self) -> None:
        self.assertIn("free", PLANS)
        self.assertIn("pro", PLANS)
        self.assertIn("enterprise", PLANS)

    def test_get_plan_unknown_falls_back_to_free(self) -> None:
        self.assertEqual(get_plan("nonsense").id, DEFAULT_PLAN_ID)
        self.assertEqual(get_plan(None).id, DEFAULT_PLAN_ID)

    def test_free_plan_has_fewer_features_than_pro(self) -> None:
        self.assertLess(len(PLANS["free"].features), len(PLANS["pro"].features))

    def test_enterprise_is_unlimited(self) -> None:
        ent = PLANS["enterprise"]
        self.assertEqual(ent.limit_for("queries"), -1)
        self.assertEqual(ent.limit_for("workspaces"), -1)

    def test_feature_enabled(self) -> None:
        self.assertTrue(feature_enabled("pro", "external_delegation"))
        self.assertFalse(feature_enabled("free", "external_delegation"))

    def test_assert_within_limit_raises_at_cap(self) -> None:
        # free plan: max_workspaces = 1
        assert_within_limit("free", "workspaces", current=0)  # ok: 0+1 <= 1
        with self.assertRaises(PlanLimitExceeded):
            assert_within_limit("free", "workspaces", current=1)  # 1+1 > 1

    def test_assert_within_limit_unlimited_never_raises(self) -> None:
        assert_within_limit("enterprise", "queries", current=10_000_000)


class TestLimitProperties(unittest.TestCase):
    @settings(max_examples=200)
    @given(
        plan_id=st.sampled_from(list(PLANS.keys())),
        metric=st.sampled_from(["queries", "uploads", "workspaces", "members", "storage_mb"]),
        current=st.integers(min_value=0, max_value=10_000),
    )
    def test_limit_enforcement_is_consistent(self, plan_id: str, metric: str, current: int) -> None:
        """Property: raises iff finite limit and current+1 > limit."""
        limit = get_plan(plan_id).limit_for(metric)
        if limit < 0 or current + 1 <= limit:
            assert_within_limit(plan_id, metric, current)  # must not raise
        else:
            with pytest.raises(PlanLimitExceeded):
                assert_within_limit(plan_id, metric, current)


if __name__ == "__main__":
    unittest.main()
