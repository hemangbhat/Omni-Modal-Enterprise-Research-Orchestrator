"""Distributed query-result cache backed by Redis.

Mirrors the public surface of :class:`omni_modal.qa.cache.QueryCache`
(``compute_key`` / ``get`` / ``set`` / ``evict_tenant`` / ``__len__``) so it is
a drop-in replacement. Storing retrieval results in Redis means a cache entry
warmed by one web instance is a hit for every other instance — and the cache
survives a single instance restart.

Serialisation: ``RetrievedChunk`` is a flat frozen dataclass, so each entry is
stored as a JSON array of ``asdict`` rows under ``{ns}:e:{key}`` with a TTL.
A per-tenant Redis SET (``{ns}:t:{tenant}``) indexes the keys so
``evict_tenant`` can purge a tenant's entries on re-ingestion without scanning
the whole keyspace.

All operations are defensive: any Redis error degrades to a cache miss (for
``get``) or a no-op (for ``set``/``evict``) so retrieval always falls back to a
live database query rather than failing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omni_modal.qa.models import RetrievedChunk

logger = logging.getLogger(__name__)


class RedisQueryCache:
    """Thread-safe-by-Redis TTL cache for retrieval results, shared across instances."""

    def __init__(self, client, *, ttl: float = 300.0, namespace: str = "qc", enabled: bool = True) -> None:
        self._r = client
        self._ttl = int(ttl)
        self._ns = namespace
        self._enabled = enabled

    @staticmethod
    def compute_key(question: str, tenant_id: str, top_k: int, min_similarity: float) -> str:
        # Reuse the canonical key derivation so Redis and in-memory caches agree.
        from omni_modal.qa.cache import QueryCache  # noqa: PLC0415

        return QueryCache.compute_key(question, tenant_id, top_k, min_similarity)

    def _entry_key(self, key: str) -> str:
        return f"{self._ns}:e:{key}"

    def _tenant_key(self, tenant_id: str) -> str:
        return f"{self._ns}:t:{tenant_id}"

    def get(self, key: str) -> "list[RetrievedChunk] | None":
        if not self._enabled:
            return None
        try:
            raw = self._r.get(self._entry_key(key))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisQueryCache.get degraded (miss): %s", exc)
            return None
        if not raw:
            return None
        try:
            from omni_modal.qa.models import RetrievedChunk  # noqa: PLC0415

            rows = json.loads(raw)
            return [RetrievedChunk(**row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisQueryCache.get deserialisation failed (miss): %s", exc)
            return None

    def set(self, key: str, chunks: "list[RetrievedChunk]", tenant_id: str = "") -> None:
        if not self._enabled:
            return
        try:
            payload = json.dumps([asdict(chunk) for chunk in chunks])
            pipe = self._r.pipeline()
            pipe.set(self._entry_key(key), payload, ex=self._ttl)
            if tenant_id:
                tkey = self._tenant_key(tenant_id)
                pipe.sadd(tkey, key)
                # Keep the index alive a little longer than the entries it tracks.
                pipe.expire(tkey, self._ttl + 60)
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisQueryCache.set degraded (ignored): %s", exc)

    def evict_tenant(self, tenant_id: str) -> int:
        if not self._enabled:
            return 0
        try:
            tkey = self._tenant_key(tenant_id)
            keys = self._r.smembers(tkey)
            if not keys:
                return 0
            pipe = self._r.pipeline()
            for k in keys:
                pipe.delete(self._entry_key(k))
            pipe.delete(tkey)
            results = pipe.execute()
            # All but the final result correspond to entry deletes.
            return sum(1 for r in results[:-1] if r)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisQueryCache.evict_tenant degraded: %s", exc)
            return 0

    def __len__(self) -> int:
        try:
            return len(self._r.keys(f"{self._ns}:e:*"))
        except Exception:  # noqa: BLE001
            return 0


def select_query_cache(*, enabled: bool | None = None):
    """Return a Redis-backed cache when Redis is available, else in-process.

    ``enabled`` defaults to the ``QUERY_CACHE_ENABLED`` env var (default true),
    matching :class:`QueryCache` semantics.
    """
    import os  # noqa: PLC0415

    from omni_modal.cache.redis_client import get_redis_client  # noqa: PLC0415
    from omni_modal.qa.cache import QueryCache  # noqa: PLC0415

    if enabled is None:
        enabled = os.environ.get("QUERY_CACHE_ENABLED", "true").lower() != "false"

    client = get_redis_client()
    if client is not None and enabled:
        logger.info("Query cache: Redis-backed (shared across instances).")
        return RedisQueryCache(client, enabled=True)
    return QueryCache(enabled=enabled)
