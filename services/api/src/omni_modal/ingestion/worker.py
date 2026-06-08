from __future__ import annotations

import logging
import queue
import threading
from dataclasses import replace
from typing import Callable

from omni_modal.ingestion.models import IngestionErrorCode, IngestionJob
from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline
from omni_modal.observability import observability

logger = logging.getLogger(__name__)


class BackgroundWorker:
    """Daemon thread that drains a queue.Queue[IngestionJob] one job at a time.

    Processes at most one job concurrently (sequential execution per worker thread).
    Unrecoverable errors are caught, the job is marked failed, and the worker continues.
    A watchdog_callback is invoked if the thread exits unexpectedly.
    """

    def __init__(
        self,
        pipeline: MultimodalIngestionPipeline,
        job_queue: "queue.Queue[IngestionJob]",
        jobs_store: "dict[str, IngestionJob]",
        jobs_lock: threading.Lock,
        *,
        watchdog_callback: "Callable[[], None] | None" = None,
        cache_evict_callback: "Callable[[str], None] | None" = None,
        entity_service: "object | None" = None,
    ) -> None:
        self._pipeline = pipeline
        self._queue = job_queue
        self._jobs = jobs_store
        self._jobs_lock = jobs_lock
        self._watchdog_callback = watchdog_callback
        self._cache_evict_callback = cache_evict_callback
        self._entity_service = entity_service
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the daemon thread. Idempotent — safe to call multiple times."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_with_watchdog,
            daemon=True,
            name="BackgroundWorker",
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the worker to drain and stop, then join the thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_with_watchdog(self) -> None:
        """Wrapper that calls watchdog_callback if thread exits unexpectedly."""
        try:
            self._run()
        except Exception as exc:
            logger.exception("BackgroundWorker crashed: %s", exc)
            observability.capture_exception(exc, operation="background_worker.crash")
            if self._watchdog_callback:
                self._watchdog_callback()

    def _run(self) -> None:
        """Main loop — runs on the daemon thread."""
        while not self._stop_event.is_set():
            try:
                job: IngestionJob = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process_one(job)
            finally:
                self._queue.task_done()

    def _process_one(self, job: IngestionJob) -> None:
        """Process a single job; update jobs_store with final status."""
        # Transition to "processing"
        with self._jobs_lock:
            processing_job = replace(job, status="processing")
            self._jobs[job.id] = processing_job

        try:
            result = self._pipeline.ingest(job.request)
            completed = replace(
                processing_job,
                status=result.status,
                result=result,
                error_code=result.error_code,
                error_message=result.error_message,
            )
            with self._jobs_lock:
                self._jobs[job.id] = completed

            # Evict cache on successful ingestion
            if result.status == "ready" and self._cache_evict_callback:
                try:
                    self._cache_evict_callback(job.request.tenant_id)
                except Exception:
                    pass  # cache eviction failure must not interrupt the worker

            # Run entity extraction on successful ingestion (non-blocking on failure)
            if result.status == "ready" and self._entity_service is not None:
                try:
                    entity_records = self._entity_service.entity_records_from_result(result)
                    if entity_records:
                        observability.add_breadcrumb(
                            message="Entity extraction completed post-ingestion",
                            category="entity_extraction",
                            level="info",
                            data={
                                "document_id": result.document_id,
                                "entity_count": len(entity_records),
                            },
                        )
                except Exception as exc:
                    # Entity extraction failures must NOT fail the ingestion job
                    observability.capture_exception(
                        exc,
                        operation="background_worker.entity_extraction",
                        context={"document_id": result.document_id},
                    )

        except Exception as exc:
            logger.exception("Unrecoverable error processing job %s: %s", job.id, exc)
            observability.capture_exception(exc, operation="background_worker.process_one")
            failed_job = replace(
                processing_job,
                status="failed",
                error_code=IngestionErrorCode.EXTRACTION_FAILED,
                error_message=str(exc),
            )
            with self._jobs_lock:
                self._jobs[job.id] = failed_job
