"""Property 13: Benchmark result serialisation completeness.
Feature: performance-and-scalability, Property 13
Validates: Requirements 6.3
"""
from __future__ import annotations

import unittest
import _path

from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.benchmark.__main__ import BenchmarkStats, serialise_stats

_REQUIRED_KEYS = frozenset({
    "timestamp",
    "retrieval_p50_ms",
    "retrieval_p95_ms",
    "retrieval_p99_ms",
    "ingestion_docs_per_minute",
})


class TestBenchmarkSerialisation(unittest.TestCase):
    """Property 13: Benchmark result serialisation completeness — Validates: Req 6.3"""

    @given(
        timestamp=st.text(min_size=1, max_size=50),
        p50=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        p95=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        p99=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
        dpm=st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_serialise_has_exactly_five_required_keys(self, timestamp, p50, p95, p99, dpm):
        """Property 13: serialise_stats() returns dict with exactly the 5 required keys."""
        stats = BenchmarkStats(
            timestamp=timestamp,
            retrieval_p50_ms=p50,
            retrieval_p95_ms=p95,
            retrieval_p99_ms=p99,
            ingestion_docs_per_minute=dpm,
        )
        result = serialise_stats(stats)
        self.assertEqual(set(result.keys()), _REQUIRED_KEYS)
        self.assertIsInstance(result["timestamp"], str)
        self.assertIsInstance(result["retrieval_p50_ms"], float)
        self.assertIsInstance(result["retrieval_p95_ms"], float)
        self.assertIsInstance(result["retrieval_p99_ms"], float)
        self.assertIsInstance(result["ingestion_docs_per_minute"], float)


if __name__ == "__main__":
    unittest.main()
