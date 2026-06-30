"""Standalone ingestion worker process (separate worker tier).

Run as its own service so the web tier stays stateless and disposable:

    python -m omni_modal.ingestion.redis_worker

Requires ``REDIS_URL`` (the durable queue) and, for real persistence,
``DATABASE_URL`` (Postgres + pgvector). It builds the same ingestion pipeline
the web tier uses, then blocks consuming jobs from Redis until terminated.

If ``REDIS_URL`` is not set this exits with a clear message rather than silently
doing nothing — a standalone worker only makes sense with a shared queue.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("omero.worker")


def _build_pipeline():
    """Mirror the web tier's pipeline construction (Postgres or in-memory)."""
    from omni_modal.entity_extraction.service import EntityExtractionService
    from omni_modal.ingestion.extractors import LocalWhisperTranscriber
    from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline
    from omni_modal.qa import select_embedding_provider

    selection = select_embedding_provider()
    provider = selection.provider
    whisper_model = os.environ.get("WHISPER_MODEL_PATH") or None
    audio = LocalWhisperTranscriber(model=whisper_model) if whisper_model else None

    if os.environ.get("DATABASE_URL"):
        from omni_modal.qa.pg_persistence import PostgresChunkPersistence

        pipeline = MultimodalIngestionPipeline(
            audio_transcriber=audio,
            persistence=PostgresChunkPersistence(
                provider,
                database_url=os.environ.get("DATABASE_URL"),
                embedding_model=selection.backend,
                dimensions=getattr(provider, "dimensions", 384),
            ),
        )
    else:
        from omni_modal.qa.in_memory_store import (
            InMemoryChunkPersistence,
            InMemoryVectorStore,
        )

        store = InMemoryVectorStore()
        pipeline = MultimodalIngestionPipeline(
            audio_transcriber=audio,
            persistence=InMemoryChunkPersistence(store, provider),
        )
        logger.warning(
            "DATABASE_URL not set — worker persists to an in-memory store that "
            "is NOT shared with the web tier. Set DATABASE_URL for real ingestion."
        )
    return pipeline, EntityExtractionService()


def main() -> int:
    from omni_modal.env_loader import load_dotenv

    load_dotenv()

    from omni_modal.cache.redis_client import get_redis_client
    from omni_modal.ingestion.redis_queue import RedisIngestionWorker
    from omni_modal.observability import observability

    observability.init()

    client = get_redis_client()
    if client is None:
        logger.error("REDIS_URL is not set or Redis is unreachable; a standalone worker requires it.")
        return 1

    pipeline, entity_service = _build_pipeline()

    # The worker can evict the shared cache directly so query results refresh
    # the moment new content is ingested.
    cache_evict = None
    try:
        from omni_modal.qa.redis_cache import RedisQueryCache

        cache_evict = RedisQueryCache(client).evict_tenant
    except Exception:  # noqa: BLE001
        pass

    worker = RedisIngestionWorker(
        client,
        pipeline,
        cache_evict_callback=cache_evict,
        entity_service=entity_service,
    )

    def _shutdown(_signum, _frame):
        logger.info("Shutdown signal received; stopping worker.")
        worker.stop()
        observability.flush()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("OMERO ingestion worker online. Waiting for jobs...")
    worker.run_blocking()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
