"""Durable, Redis-backed ingestion queue with a separable worker tier.

Replaces :class:`AsyncIngestionQueue` (in-process ``queue.Queue`` + daemon
thread) when ``REDIS_URL`` is configured. The public surface is identical
(``enqueue`` / ``get`` / ``fail`` / ``start_worker``) so callers don't change.

What this buys over the in-process queue:
  * **Durability** — jobs live in Redis, so a web restart mid-flight does not
    lose queued work. A crashed worker's in-flight job is recoverable from the
    ``processing`` list.
  * **Separate worker tier** — the same :class:`RedisIngestionWorker` runs
    either as an in-process daemon thread (single-container deploys) or as a
    standalone process (``python -m omni_modal.ingestion.redis_worker``) so the
    web tier can stay stateless and disposable.
  * **Retries + dead-letter** — transient failures are retried up to
    ``max_retries``; exhausted jobs land on a dead-letter list for inspection.

Payload handling: because a standalone worker runs in a different container
from the web tier (no shared filesystem), the uploaded file bytes are stored in
Redis (base64) under a short TTL and rehydrated to a temp file by the worker.
For very large media, swap this for the existing S3 storage adapter — the
queue only needs the object key. This is documented as the next scaling step.

Reliability model: at-least-once delivery. ``pipeline.ingest`` is idempotent
(documents/chunks/embeddings use ``ON CONFLICT DO UPDATE``), so a re-delivered
job converges to the same state.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable
from uuid import uuid4

from omni_modal.ingestion.models import IngestionErrorCode, IngestionJob, IngestionRequest
from omni_modal.ingestion.pipeline import (
    MultimodalIngestionPipeline,
    deserialize_ingestion_result,
    serialize_ingestion_result,
)
from omni_modal.observability import observability

logger = logging.getLogger(__name__)

QUEUE_KEY = "omero:ingest:pending"
PROCESSING_KEY = "omero:ingest:processing"
DEADLETTER_KEY = "omero:ingest:deadletter"
JOB_PREFIX = "omero:ingest:job:"
PAYLOAD_PREFIX = "omero:ingest:payload:"

JOB_TTL_SECONDS = 7 * 24 * 3600
PAYLOAD_TTL_SECONDS = 24 * 3600


def _request_to_json(req: IngestionRequest) -> str:
    return json.dumps(
        {
            "tenant_id": req.tenant_id,
            "document_id": req.document_id,
            "owner_id": req.owner_id,
            "file_path": str(req.file_path),
            "source_kind": req.source_kind,
            "title": req.title,
        }
    )


def _request_from_json(raw: str, *, file_path_override: str | None = None) -> IngestionRequest:
    d = json.loads(raw)
    return IngestionRequest(
        tenant_id=d["tenant_id"],
        document_id=d["document_id"],
        owner_id=d["owner_id"],
        file_path=Path(file_path_override or d["file_path"]),
        source_kind=d.get("source_kind"),
        title=d.get("title"),
    )


class RedisIngestionQueue:
    """Durable ingestion queue sharing job state across instances via Redis."""

    def __init__(
        self,
        client,
        pipeline: MultimodalIngestionPipeline | None = None,
        *,
        max_retries: int = 3,
        cache_evict_callback: "Callable[[str], None] | None" = None,
        entity_service: "object | None" = None,
        in_process_worker: bool | None = None,
    ) -> None:
        self._r = client
        self._pipeline = pipeline or MultimodalIngestionPipeline()
        self._max_retries = max_retries
        self._cache_evict_callback = cache_evict_callback
        self._entity_service = entity_service
        if in_process_worker is None:
            in_process_worker = os.environ.get("INGEST_WORKER_IN_PROCESS", "true").lower() != "false"
        self._in_process_worker = in_process_worker
        self._worker: RedisIngestionWorker | None = None

    # ── enqueue / read ────────────────────────────────────────────────────
    def enqueue(self, request: IngestionRequest) -> IngestionJob:
        job_id = str(uuid4())
        job = IngestionJob(id=job_id, request=request, status="uploaded")

        # Store the file bytes so a separate-container worker can read them.
        try:
            data = Path(request.file_path).read_bytes()
            self._r.set(
                PAYLOAD_PREFIX + job_id,
                base64.b64encode(data).decode("ascii"),
                ex=PAYLOAD_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - worker will fail the job cleanly
            logger.warning("Could not stage payload for job %s: %s", job_id, exc)

        self._write_job(job, attempts=0)
        self._r.lpush(QUEUE_KEY, job_id)
        return job

    def get(self, job_id: str) -> IngestionJob | None:
        try:
            data = self._r.hgetall(JOB_PREFIX + job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisIngestionQueue.get degraded: %s", exc)
            return None
        if not data:
            return None
        return self._job_from_map(job_id, data)

    def fail(self, job_id: str, error_code: IngestionErrorCode, error_message: str) -> IngestionJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        failed = replace(job, status="failed", error_code=error_code, error_message=error_message)
        self._write_job(failed, attempts=self._attempts(job_id))
        return failed

    # ── worker lifecycle ────────────────────────────────────────────────────
    def start_worker(self) -> "RedisIngestionWorker":
        """Create the worker. Starts an in-process consumer thread unless a
        separate worker tier is deployed (``INGEST_WORKER_IN_PROCESS=false``)."""
        self._worker = RedisIngestionWorker(
            self._r,
            self._pipeline,
            max_retries=self._max_retries,
            cache_evict_callback=self._cache_evict_callback,
            entity_service=self._entity_service,
        )
        if self._in_process_worker:
            self._worker.start()
        else:
            logger.info(
                "INGEST_WORKER_IN_PROCESS=false — web tier will not consume; "
                "run 'python -m omni_modal.ingestion.redis_worker' as a worker service."
            )
        return self._worker

    # ── internal serialisation ──────────────────────────────────────────────
    def _attempts(self, job_id: str) -> int:
        try:
            raw = self._r.hget(JOB_PREFIX + job_id, "attempts")
            return int(raw) if raw else 0
        except Exception:  # noqa: BLE001
            return 0

    def _write_job(self, job: IngestionJob, *, attempts: int) -> None:
        mapping = {
            "request": _request_to_json(job.request),
            "status": job.status,
            "attempts": str(attempts),
            "error_code": job.error_code.value if job.error_code else "",
            "error_message": job.error_message or "",
            "result": json.dumps(serialize_ingestion_result(job.result)) if job.result else "",
        }
        pipe = self._r.pipeline()
        pipe.hset(JOB_PREFIX + job.id, mapping=mapping)
        pipe.expire(JOB_PREFIX + job.id, JOB_TTL_SECONDS)
        pipe.execute()

    def _job_from_map(self, job_id: str, data: dict) -> IngestionJob:
        request = _request_from_json(data["request"])
        result = None
        if data.get("result"):
            try:
                result = deserialize_ingestion_result(json.loads(data["result"]))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not deserialise result for job %s: %s", job_id, exc)
        error_code = IngestionErrorCode(data["error_code"]) if data.get("error_code") else None
        return IngestionJob(
            id=job_id,
            request=request,
            status=data.get("status", "uploaded"),  # type: ignore[arg-type]
            result=result,
            error_code=error_code,
            error_message=data.get("error_message") or None,
        )


class RedisIngestionWorker:
    """Consumes jobs from the durable Redis queue, one at a time.

    Runs either as a daemon thread (``start()``) inside the web process or as a
    blocking standalone process (``run_blocking()``) for a dedicated worker tier.
    """

    def __init__(
        self,
        client,
        pipeline: MultimodalIngestionPipeline,
        *,
        max_retries: int = 3,
        cache_evict_callback: "Callable[[str], None] | None" = None,
        entity_service: "object | None" = None,
        poll_timeout: int = 2,
    ) -> None:
        self._r = client
        self._pipeline = pipeline
        self._max_retries = max_retries
        self._cache_evict_callback = cache_evict_callback
        self._entity_service = entity_service
        self._poll_timeout = poll_timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_forever, daemon=True, name="RedisIngestionWorker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_blocking(self) -> None:  # pragma: no cover - exercised by the worker process
        logger.info("RedisIngestionWorker started (blocking mode).")
        self._run_forever()

    def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self._consume_once(block=True)
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                logger.exception("Worker loop error: %s", exc)
                observability.capture_exception(exc, operation="redis_worker.loop")
                time.sleep(1.0)

    # ── consumption ───────────────────────────────────────────────────────
    def _consume_once(self, *, block: bool = False) -> str | None:
        """Move one job from pending→processing and process it.

        ``block=True`` uses a blocking pop (efficient against real Redis);
        ``block=False`` is non-blocking and used by tests.
        """
        if block:
            job_id = self._r.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=self._poll_timeout)
        else:
            job_id = self._r.rpoplpush(QUEUE_KEY, PROCESSING_KEY)
        if not job_id:
            return None
        try:
            self._process(job_id)
        finally:
            self._r.lrem(PROCESSING_KEY, 1, job_id)
        return job_id

    def drain(self, max_jobs: int = 1000) -> int:
        """Process all currently-pending jobs (non-blocking). Test helper."""
        processed = 0
        while processed < max_jobs and self._consume_once(block=False) is not None:
            processed += 1
        return processed

    def _process(self, job_id: str) -> None:
        data = self._r.hgetall(JOB_PREFIX + job_id)
        if not data:
            logger.warning("Job %s vanished before processing.", job_id)
            return

        attempts = int(data.get("attempts", "0"))
        self._set_status(job_id, "processing", attempts=attempts)

        # Rehydrate the staged payload to a temp file the pipeline can read.
        tmp_path: str | None = None
        try:
            raw_payload = self._r.get(PAYLOAD_PREFIX + job_id)
            if raw_payload:
                suffix = Path(json.loads(data["request"]).get("file_path", "")).suffix
                fd, tmp_path = tempfile.mkstemp(suffix=suffix or None)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(base64.b64decode(raw_payload))
            request = _request_from_json(data["request"], file_path_override=tmp_path)

            result = self._pipeline.ingest(request)
            self._finish(job_id, result, attempts)
        except Exception as exc:  # noqa: BLE001
            self._handle_failure(job_id, attempts, exc)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _finish(self, job_id: str, result, attempts: int) -> None:
        pipe = self._r.pipeline()
        pipe.hset(
            JOB_PREFIX + job_id,
            mapping={
                "status": result.status,
                "result": json.dumps(serialize_ingestion_result(result)),
                "error_code": result.error_code.value if result.error_code else "",
                "error_message": result.error_message or "",
                "attempts": str(attempts),
            },
        )
        pipe.expire(JOB_PREFIX + job_id, JOB_TTL_SECONDS)
        pipe.delete(PAYLOAD_PREFIX + job_id)
        pipe.execute()

        if result.status == "ready":
            if self._cache_evict_callback:
                try:
                    self._cache_evict_callback(result.tenant_id)
                except Exception:  # noqa: BLE001
                    pass
            if self._entity_service is not None:
                try:
                    self._entity_service.entity_records_from_result(result)
                except Exception as exc:  # noqa: BLE001
                    observability.capture_exception(
                        exc, operation="redis_worker.entity_extraction"
                    )

    def _handle_failure(self, job_id: str, attempts: int, exc: BaseException) -> None:
        attempts += 1
        logger.exception("Job %s failed (attempt %d): %s", job_id, attempts, exc)
        observability.capture_exception(exc, operation="redis_worker.process")

        if attempts < self._max_retries:
            # Transient — retry by re-queuing.
            self._set_status(job_id, "uploaded", attempts=attempts)
            self._r.lpush(QUEUE_KEY, job_id)
            return

        # Exhausted — terminal failure + dead-letter.
        pipe = self._r.pipeline()
        pipe.hset(
            JOB_PREFIX + job_id,
            mapping={
                "status": "failed",
                "error_code": IngestionErrorCode.EXTRACTION_FAILED.value,
                "error_message": str(exc),
                "attempts": str(attempts),
            },
        )
        pipe.delete(PAYLOAD_PREFIX + job_id)
        pipe.lpush(DEADLETTER_KEY, job_id)
        pipe.execute()

    def _set_status(self, job_id: str, status: str, *, attempts: int) -> None:
        self._r.hset(
            JOB_PREFIX + job_id,
            mapping={"status": status, "attempts": str(attempts)},
        )


def select_ingestion_queue(
    pipeline: MultimodalIngestionPipeline | None = None,
    *,
    cache_evict_callback: "Callable[[str], None] | None" = None,
    entity_service: "object | None" = None,
):
    """Return the durable Redis queue when Redis is available, else the
    in-process :class:`AsyncIngestionQueue`. Both expose enqueue/get/fail/
    start_worker, so callers are agnostic to which is active."""
    from omni_modal.cache.redis_client import get_redis_client  # noqa: PLC0415

    client = get_redis_client()
    if client is not None:
        logger.info("Ingestion queue: Redis-backed (durable, worker-tier ready).")
        return RedisIngestionQueue(
            client,
            pipeline,
            cache_evict_callback=cache_evict_callback,
            entity_service=entity_service,
        )

    from omni_modal.ingestion.async_queue import AsyncIngestionQueue  # noqa: PLC0415

    return AsyncIngestionQueue(
        pipeline,
        cache_evict_callback=cache_evict_callback,
        entity_service=entity_service,
    )
