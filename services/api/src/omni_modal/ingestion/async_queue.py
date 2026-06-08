from __future__ import annotations

import logging
import queue
import threading
from dataclasses import replace
from typing import Callable
from uuid import uuid4

from omni_modal.ingestion.models import IngestionErrorCode, IngestionJob, IngestionRequest
from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline
from omni_modal.ingestion.worker import BackgroundWorker
from omni_modal.observability import observability
logger = logging.getLogger(__name__)


class AsyncIngestionQueue:
    """Thread-safe ingestion queue backed by a BackgroundWorker daemon thread.

    Replaces InMemoryIngestionQueue for Phase 11. Retains the same
    enqueue/get/fail interface but returns 202 immediately on enqueue.
    """

    def __init__(
        self,
        pipeline: MultimodalIngestionPipeline | None = None,
        *,
        max_queue_size: int = 0,  # 0 = unbounded
        cache_evict_callback: "Callable[[str], None] | None" = None,
        entity_service: "object | None" = None,
    ) -> None:
        self._pipeline = pipeline or MultimodalIngestionPipeline()
        self._max_queue_size = max_queue_size
        self._cache_evict_callback = cache_evict_callback
        self._entity_service = entity_service
        self._queue: queue.Queue[IngestionJob] = queue.Queue(maxsize=max_queue_size)
        self._jobs: dict[str, IngestionJob] = {}
        self._jobs_lock = threading.Lock()
        self._worker: BackgroundWorker | None = None

    def enqueue(self, request: IngestionRequest) -> IngestionJob:
        """Create a job, store it, push it onto the queue, return immediately.

        Raises queue.Full if a bounded queue is at capacity.
        """
        job = IngestionJob(id=str(uuid4()), request=request, status="uploaded")
        with self._jobs_lock:
            self._jobs[job.id] = job
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            # Remove from jobs_store and re-raise
            with self._jobs_lock:
                self._jobs.pop(job.id, None)
            raise
        return job

    def get(self, job_id: str) -> IngestionJob | None:
        """Return current job state (thread-safe read)."""
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def fail(
        self,
        job_id: str,
        error_code: IngestionErrorCode,
        error_message: str,
    ) -> IngestionJob:
        """Explicitly fail a job (used by tests and error injection)."""
        with self._jobs_lock:
            job = self._jobs[job_id]
            failed = replace(
                job,
                status="failed",
                error_code=error_code,
                error_message=error_message,
            )
            self._jobs[job_id] = failed
            return failed

    def start_worker(self) -> BackgroundWorker:
        """Create and start the BackgroundWorker daemon thread."""

        def _watchdog() -> None:
            logger.warning("BackgroundWorker exited unexpectedly; restarting in 5 s.")
            observability.capture_message(
                "BackgroundWorker exited unexpectedly; restarting.",
                operation="background_worker.watchdog",
                level="warning",
            )
            if self._worker:
                self._worker.start()

        self._worker = BackgroundWorker(
            pipeline=self._pipeline,
            job_queue=self._queue,
            jobs_store=self._jobs,
            jobs_lock=self._jobs_lock,
            watchdog_callback=_watchdog,
            cache_evict_callback=self._cache_evict_callback,
            entity_service=self._entity_service,
        )
        self._worker.start()
        return self._worker
