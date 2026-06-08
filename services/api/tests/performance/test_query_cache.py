"""Tests for qa/cache.py — QueryCache.

Properties:
  - Property 5: Cache key determinism and collision resistance (Req 4.1)
  - Property 6: Cache hit bypasses DB (Req 4.2)
  - Property 7: Cache miss stores result round-trip (Req 4.3)
  - Property 8: Tenant cache eviction completeness (Req 4.4)
  - Property 9: Cache size bounded by maxsize (Req 4.5)
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import _path  # noqa: F401
from hypothesis import given, settings, assume
import hypothesis.strategies as st

from omni_modal.qa.cache import QueryCache, CacheKey


def _make_chunk(content: str = "chunk", chunk_id: str = "c1") -> object:
    from omni_modal.qa.models import RetrievedChunk
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc1",
        title="Title",
        source_type="pdf",
        chunk_index=0,
        content=content,
        similarity=0.9,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Property 5: Cache key determinism and collision resistance
# Validates: Requirements 4.1
# ---------------------------------------------------------------------------

class TestCacheKeyDeterminism(unittest.TestCase):
    """Property 5: Cache key determinism and collision resistance"""

    @given(
        question=st.text(max_size=500),
        tenant_id=st.text(max_size=100),
        top_k=st.integers(min_value=1, max_value=50),
        min_sim=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_same_inputs_same_key(self, question, tenant_id, top_k, min_sim):
        """Property 5a: Same inputs always produce the same key."""
        k1 = QueryCache.compute_key(question, tenant_id, top_k, min_sim)
        k2 = QueryCache.compute_key(question, tenant_id, top_k, min_sim)
        self.assertEqual(k1, k2)

    @given(
        q1=st.text(max_size=100),
        q2=st.text(max_size=100),
        tenant=st.text(max_size=50),
        top_k=st.integers(1, 20),
        min_sim=st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_different_questions_different_keys(self, q1, q2, tenant, top_k, min_sim):
        """Property 5b: Distinct questions produce distinct keys."""
        assume(q1.lower().strip() != q2.lower().strip())
        k1 = QueryCache.compute_key(q1, tenant, top_k, min_sim)
        k2 = QueryCache.compute_key(q2, tenant, top_k, min_sim)
        self.assertNotEqual(k1, k2)


# ---------------------------------------------------------------------------
# Property 6: Cache hit bypasses DB
# Validates: Requirements 4.2
# ---------------------------------------------------------------------------

class TestCacheHitBypassesDB(unittest.TestCase):
    """Property 6: Cache hit bypasses the database"""

    @given(
        chunks_count=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=100)
    def test_cache_hit_returns_cached_without_db(self, chunks_count):
        """Property 6: cache hit doesn't touch DB."""
        cache = QueryCache(maxsize=256, ttl=300.0, enabled=True)
        key = QueryCache.compute_key("question", "tenant", 5, 0.5)
        chunks = [_make_chunk(f"chunk{i}", f"c{i}") for i in range(chunks_count)]
        cache.set(key, chunks, tenant_id="tenant")

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock()
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        result = cache.get(key)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), chunks_count)
        mock_pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# Property 7: Cache miss stores result round-trip
# Validates: Requirements 4.3
# ---------------------------------------------------------------------------

class TestCacheMissStoresResult(unittest.TestCase):
    """Property 7: Cache miss stores result (round-trip)"""

    @given(
        chunks_count=st.integers(min_value=1, max_value=10),
        question=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=100)
    def test_cache_miss_stores_after_set(self, chunks_count, question):
        """Property 7: after set(), get() returns same result."""
        cache = QueryCache(maxsize=256, ttl=300.0, enabled=True)
        key = QueryCache.compute_key(question, "tenant", 5, 0.5)

        self.assertIsNone(cache.get(key))  # cache miss

        chunks = [_make_chunk(f"chunk{i}", f"c{i}") for i in range(chunks_count)]
        cache.set(key, chunks, tenant_id="tenant")

        result = cache.get(key)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), chunks_count)


# ---------------------------------------------------------------------------
# Property 8: Tenant cache eviction completeness
# Validates: Requirements 4.4
# ---------------------------------------------------------------------------

class TestTenantEviction(unittest.TestCase):
    """Property 8: Tenant cache eviction is complete"""

    @given(
        tenant_id=st.text(min_size=1, max_size=20),
        queries=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_evict_tenant_removes_all_entries(self, tenant_id, queries):
        """Property 8: after evict_tenant(T), all T's keys return None."""
        cache = QueryCache(maxsize=512, ttl=300.0, enabled=True)
        keys = []
        for i, q in enumerate(queries):
            key = QueryCache.compute_key(q, tenant_id, i + 1, 0.5)
            cache.set(key, [_make_chunk(f"c{i}", f"id{i}")], tenant_id=tenant_id)
            keys.append(key)

        cache.evict_tenant(tenant_id)

        for key in keys:
            result = cache.get(key)
            self.assertIsNone(result, f"Key {key[:8]}... still present after eviction")


# ---------------------------------------------------------------------------
# Property 9: Cache size bounded by maxsize
# Validates: Requirements 4.5
# ---------------------------------------------------------------------------

class TestCacheSizeBounded(unittest.TestCase):
    """Property 9: Cache size is bounded by maxsize"""

    @given(
        maxsize=st.integers(min_value=1, max_value=100),
        insert_count=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=100)
    def test_cache_size_never_exceeds_maxsize(self, maxsize, insert_count):
        """Property 9: len(cache) <= maxsize at all times."""
        cache = QueryCache(maxsize=maxsize, ttl=300.0, enabled=True)
        for i in range(insert_count):
            key = QueryCache.compute_key(f"question {i}", f"tenant{i}", 5, 0.5)
            cache.set(key, [_make_chunk()], tenant_id=f"tenant{i}")
            self.assertLessEqual(len(cache), maxsize)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestQueryCacheUnit(unittest.TestCase):

    def test_disabled_cache_always_returns_none(self):
        cache = QueryCache(enabled=False)
        key = QueryCache.compute_key("q", "t", 5, 0.0)
        cache.set(key, [_make_chunk()], tenant_id="t")
        self.assertIsNone(cache.get(key))

    def test_disabled_cache_len_is_zero(self):
        cache = QueryCache(enabled=False)
        self.assertEqual(len(cache), 0)

    def test_evict_nonexistent_tenant_returns_zero(self):
        cache = QueryCache()
        evicted = cache.evict_tenant("nonexistent-tenant")
        self.assertEqual(evicted, 0)

    def test_case_insensitive_key(self):
        """Uppercase and lowercase question produce same key."""
        k1 = QueryCache.compute_key("Hello World", "t", 5, 0.5)
        k2 = QueryCache.compute_key("hello world", "t", 5, 0.5)
        self.assertEqual(k1, k2)

    def test_whitespace_stripped_key(self):
        k1 = QueryCache.compute_key("  hello  ", "t", 5, 0.5)
        k2 = QueryCache.compute_key("hello", "t", 5, 0.5)
        self.assertEqual(k1, k2)

    def test_get_returns_none_on_miss(self):
        cache = QueryCache(maxsize=10, ttl=300.0, enabled=True)
        result = cache.get("nonexistent-key")
        self.assertIsNone(result)

    def test_set_and_get_roundtrip_with_real_chunk(self):
        cache = QueryCache(maxsize=10, ttl=300.0, enabled=True)
        k = QueryCache.compute_key("hello world", "tenant-1", 10, 0.7)
        chunk = _make_chunk("test content", "c1")
        cache.set(k, [chunk], tenant_id="tenant-1")
        result = cache.get(k)
        self.assertEqual(result, [chunk])

    def test_evict_tenant_returns_count(self):
        cache = QueryCache(maxsize=50, ttl=300.0, enabled=True)
        for i in range(5):
            k = QueryCache.compute_key(f"q{i}", "tenant-a", 5, 0.5)
            cache.set(k, [_make_chunk(f"c{i}", f"id{i}")], tenant_id="tenant-a")
        count = cache.evict_tenant("tenant-a")
        self.assertEqual(count, 5)
        self.assertEqual(len(cache), 0)

    def test_evict_other_tenant_not_affected(self):
        cache = QueryCache(maxsize=50, ttl=300.0, enabled=True)
        k_a = QueryCache.compute_key("qa", "tenant-a", 5, 0.5)
        k_b = QueryCache.compute_key("qb", "tenant-b", 5, 0.5)
        cache.set(k_a, [_make_chunk("a", "ca")], tenant_id="tenant-a")
        cache.set(k_b, [_make_chunk("b", "cb")], tenant_id="tenant-b")
        cache.evict_tenant("tenant-a")
        self.assertIsNone(cache.get(k_a))
        self.assertIsNotNone(cache.get(k_b))


if __name__ == "__main__":
    unittest.main()
