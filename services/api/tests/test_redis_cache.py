"""Tests for the Redis-backed distributed query cache (Phase 1 scaling)."""

from __future__ import annotations

import pytest

fakeredis = pytest.importorskip("fakeredis")

from omni_modal.qa.cache import QueryCache
from omni_modal.qa.models import RetrievedChunk
from omni_modal.qa.redis_cache import RedisQueryCache, select_query_cache


@pytest.fixture()
def client():
    return fakeredis.FakeRedis(decode_responses=True)


def _chunk(i: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{i}",
        document_id="doc-1",
        title="Doc 1",
        source_type="pdf",
        chunk_index=i,
        content=f"content {i}",
        similarity=0.9 - i * 0.01,
        metadata={"page_number": i},
    )


def test_compute_key_matches_in_memory_cache():
    a = RedisQueryCache.compute_key("Hello", "t1", 5, 0.0)
    b = QueryCache.compute_key("Hello", "t1", 5, 0.0)
    assert a == b


def test_set_get_round_trip(client):
    cache = RedisQueryCache(client)
    key = RedisQueryCache.compute_key("q", "t1", 5, 0.0)
    chunks = [_chunk(0), _chunk(1)]
    cache.set(key, chunks, tenant_id="t1")
    got = cache.get(key)
    assert got == chunks


def test_get_miss_returns_none(client):
    cache = RedisQueryCache(client)
    assert cache.get("does-not-exist") is None


def test_evict_tenant_removes_all_entries(client):
    cache = RedisQueryCache(client)
    k1 = RedisQueryCache.compute_key("q1", "t1", 5, 0.0)
    k2 = RedisQueryCache.compute_key("q2", "t1", 5, 0.0)
    other = RedisQueryCache.compute_key("q3", "t2", 5, 0.0)
    cache.set(k1, [_chunk(0)], tenant_id="t1")
    cache.set(k2, [_chunk(1)], tenant_id="t1")
    cache.set(other, [_chunk(2)], tenant_id="t2")

    evicted = cache.evict_tenant("t1")
    assert evicted == 2
    assert cache.get(k1) is None
    assert cache.get(k2) is None
    # Other tenants are untouched.
    assert cache.get(other) is not None


def test_disabled_cache_is_noop(client):
    cache = RedisQueryCache(client, enabled=False)
    key = RedisQueryCache.compute_key("q", "t1", 5, 0.0)
    cache.set(key, [_chunk(0)], tenant_id="t1")
    assert cache.get(key) is None


def test_len_counts_entries(client):
    cache = RedisQueryCache(client)
    cache.set(RedisQueryCache.compute_key("a", "t1", 5, 0.0), [_chunk(0)], tenant_id="t1")
    cache.set(RedisQueryCache.compute_key("b", "t1", 5, 0.0), [_chunk(1)], tenant_id="t1")
    assert len(cache) == 2


def test_get_degrades_to_miss_on_error():
    class BrokenClient:
        def get(self, *_a, **_k):
            raise RuntimeError("redis down")

    cache = RedisQueryCache(BrokenClient())
    assert cache.get("k") is None


def test_select_query_cache_prefers_redis(client):
    from omni_modal.cache import redis_client

    redis_client.set_test_client(client)
    try:
        cache = select_query_cache()
        assert isinstance(cache, RedisQueryCache)
    finally:
        redis_client.set_test_client(None)


def test_select_query_cache_falls_back(monkeypatch):
    from omni_modal.cache import redis_client

    redis_client.set_test_client(None)
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_client.reset_for_testing()
    cache = select_query_cache()
    assert isinstance(cache, QueryCache)
