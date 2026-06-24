from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from omni_modal.qa.models import RetrievedChunk

logger = logging.getLogger(__name__)

CacheKey: TypeAlias = str

try:
    from cachetools import TTLCache  # type: ignore[import-not-found]
    _CACHETOOLS_AVAILABLE = True
except ImportError:
    TTLCache = None  # type: ignore[assignment,misc]
    _CACHETOOLS_AVAILABLE = False


class QueryCache:
    """Thread-safe LRU + TTL cache for retrieval results.

    Uses cachetools.TTLCache under the hood. The cache is keyed on a
    SHA-256 digest of (question_normalised, tenant_id, top_k, min_similarity).

    A secondary _tenant_keys index enables O(|tenant_entries|)
    evict_tenant() without scanning the entire cache.

    If cachetools is not installed, the cache operates in disabled mode (always miss).
    """

    def __init__(
        self,
        *,
        maxsize: int = 256,
        ttl: float = 300.0,
        enabled: bool | None = None,
    ) -> None:
        # When ``enabled`` is not explicitly provided, resolve it from the
        # QUERY_CACHE_ENABLED environment variable (default "true").  Setting
        # QUERY_CACHE_ENABLED=false disables the cache so the retriever always
        # executes a live database query (Requirement 4.6).
        if enabled is None:
            enabled = os.environ.get("QUERY_CACHE_ENABLED", "true").lower() != "false"
        self._enabled = enabled
        self._lock = threading.Lock()
        self._tenant_keys: dict[str, set[CacheKey]] = {}

        if enabled and _CACHETOOLS_AVAILABLE:
            self._cache: TTLCache | None = TTLCache(maxsize=maxsize, ttl=ttl)
        elif enabled and not _CACHETOOLS_AVAILABLE:
            logger.warning("cachetools not installed; QueryCache disabled.")
            self._cache = None
            self._enabled = False
        else:
            self._cache = None

        self._maxsize = maxsize

    @staticmethod
    def compute_key(
        question: str,
        tenant_id: str,
        top_k: int,
        min_similarity: float,
    ) -> CacheKey:
        """Return the SHA-256 hex digest of the canonical key tuple.

        ``question`` is lower-cased and stripped before hashing so that
        trivially equivalent queries share a cache entry.
        """
        normalized = question.lower().strip()
        raw = json.dumps(
            [normalized, tenant_id, top_k, min_similarity],
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    def get(self, key: CacheKey) -> "list[RetrievedChunk] | None":
        """Return cached chunks or None on miss / disabled."""
        if not self._enabled or self._cache is None:
            return None
        try:
            with self._lock:
                return self._cache.get(key)
        except Exception as exc:
            logger.warning("QueryCache.get() internal error (returning None): %s", exc)
            return None

    def set(self, key: CacheKey, chunks: "list[RetrievedChunk]", tenant_id: str = "") -> None:
        """Store chunks under key; no-op when disabled.

        tenant_id is used to populate the secondary eviction index.
        """
        if not self._enabled or self._cache is None:
            return
        try:
            with self._lock:
                self._cache[key] = chunks
                if tenant_id:
                    if tenant_id not in self._tenant_keys:
                        self._tenant_keys[tenant_id] = set()
                    self._tenant_keys[tenant_id].add(key)
        except Exception as exc:
            logger.warning("QueryCache.set() internal error (ignoring): %s", exc)

    def evict_tenant(self, tenant_id: str) -> int:
        """Remove all entries whose key was derived from tenant_id.

        Returns the number of evicted entries.
        """
        if not self._enabled or self._cache is None:
            return 0
        try:
            with self._lock:
                keys_to_evict = self._tenant_keys.pop(tenant_id, set())
                evicted = 0
                for key in keys_to_evict:
                    if key in self._cache:
                        del self._cache[key]
                        evicted += 1
                return evicted
        except Exception as exc:
            logger.warning("QueryCache.evict_tenant() internal error: %s", exc)
            return 0

    def __len__(self) -> int:
        if not self._enabled or self._cache is None:
            return 0
        try:
            with self._lock:
                return len(self._cache)
        except Exception:
            return 0
