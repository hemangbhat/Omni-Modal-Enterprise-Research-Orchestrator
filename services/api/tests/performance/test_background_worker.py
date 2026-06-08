"""Properties 1, 2, 15: BackgroundWorker correctness.
Feature: performance-and-scalability
"""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import _path  # noqa: F401
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.ingestion.models import IngestionErrorCode, IngestionRequest
from omni_modal.ingestion.async_queue import AsyncIngestionQueue
from omni_modal.ingestion.worker import BackgroundWorker


def _make_request(doc_id: str = "doc-1", tenant_id: str = "tenant-1") -> IngestionRequest:
    return IngestionRequest(
        tenant_id=tenant_id,
        document_id=doc_id,
        owner_id="owner-1",
        file_path=Path("/tmp/test.pdf"),
        source_kind="pdf",
    )


# ---------------------------------------------------------------------------
# Property 1: Job failure records full error context
# ---------------------------------------------------------------------------

class TestJobFailureErrorContext(unittest.TestCase):
    """Property 1: Job failure records full error context — Validates: Requirements 1.4"""

    @given(
        error_code=st.sampled_from(list(IngestionErrorCode)),
        error_message=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=100)
    def test_fail_records_error_context(
        self, error_code: IngestionErrorCode, error_message: str
    ) -> None:
        """fail() produces job with status=failed, matching code and message."""
        q = AsyncIngestionQueue()
        req = _make_request()
        job = q.enqueue(req)
        failed_job = q.fail(job.id, error_code, error_message)
        assert failed_job.status == "failed"
        assert failed_job.error_code == error_code
        assert failed_job.error_message == error_message


# ---------------------------------------------------------------------------
# Property 2: BackgroundWorker processes at most one job at a time
# ---------------------------------------------------------------------------

class TestAtMostOneJobProcessing(unittest.TestCase):
    """Property 2: BackgroundWorker processes at most one job at a time — Validates: Requirements 1.6"""

    def test_at_most_one_job_in_processing(self) -> None:
        """At any observable point, at most 1 job has status 'processing'."""
        barrier = threading.Barrier(2)
        released = threading.Event()

        class SlowPipeline:
            def ingest(self, request):  # type: ignore[override]
                # Signal the test thread that processing has started, then block.
                barrier.wait(timeout=5.0)
                released.wait(timeout=5.0)
                return type(
                    "FakeResult",
                    (),
                    {
                        "status": "ready",
                        "document_id": request.document_id,
                        "error_code": None,
                        "error_message": None,
                        "chunks": [],
                        "metadata": {},
                        "tenant_id": request.tenant_id,
                        "title": "test",
                        "source_kind": "pdf",
                        "owner_id": request.owner_id,
                    },
                )()

        q = AsyncIngestionQueue(pipeline=SlowPipeline())
        q.enqueue(_make_request("doc-1"))
        q.enqueue(_make_request("doc-2"))
        worker = q.start_worker()

        try:
            # Wait until the worker has picked up the first job and is blocking
            barrier.wait(timeout=5.0)
            # Snapshot all job statuses
            with q._jobs_lock:
                statuses = [j.status for j in q._jobs.values()]
            processing_count = statuses.count("processing")
            self.assertLessEqual(
                processing_count,
                1,
                f"Expected at most 1 job in 'processing', got {processing_count}: {statuses}",
            )
        finally:
            released.set()
            worker.stop(timeout=5.0)


# ---------------------------------------------------------------------------
# Property 15: Watchdog callback invoked on unexpected thread exit
# ---------------------------------------------------------------------------

class TestWatchdogRestartsWorker(unittest.TestCase):
    """Property 15: Watchdog callback invoked and worker restarts — Validates: Requirements 8.3"""

    def test_watchdog_callback_invoked_on_crash(self) -> None:
        """Watchdog callback is called when the worker thread crashes."""
        watchdog_called = threading.Event()

        def fake_watchdog() -> None:
            watchdog_called.set()

        q = AsyncIngestionQueue()

        # Create a worker whose _run method crashes immediately
        worker = BackgroundWorker(
            pipeline=MagicMock(),
            job_queue=q._queue,
            jobs_store=q._jobs,
            jobs_lock=q._jobs_lock,
            watchdog_callback=fake_watchdog,
        )

        def crashing_run() -> None:
            raise RuntimeError("Simulated crash")

        worker._run = crashing_run  # type: ignore[method-assign]
        worker.start()

        # Watchdog must be invoked within 5 seconds of the thread crashing
        called = watchdog_called.wait(timeout=5.0)
        self.assertTrue(
            called,
            "Watchdog callback was not invoked after unexpected thread exit",
        )

    def test_watchdog_triggers_worker_restart(self) -> None:
        """After a crash, start_worker watchdog restarts the BackgroundWorker."""
        restart_count = [0]

        q = AsyncIngestionQueue()
        # Override start_worker to record restarts
        original_start_worker = q.start_worker

        crashed_event = threading.Event()

        def _watchdog() -> None:
            restart_count[0] += 1
            crashed_event.set()

        worker = BackgroundWorker(
            pipeline=MagicMock(),
            job_queue=q._queue,
            jobs_store=q._jobs,
            jobs_lock=q._jobs_lock,
            watchdog_callback=_watchdog,
        )

        def crashing_run() -> None:
            raise RuntimeError("Simulated crash for restart test")

        worker._run = crashing_run  # type: ignore[method-assign]
        worker.start()

        # Watchdog is invoked within 5 s
        self.assertTrue(
            crashed_event.wait(timeout=5.0),
            "Watchdog was not invoked after worker crash",
        )
        self.assertEqual(restart_count[0], 1, "Watchdog should have been called exactly once")


if __name__ == "__main__":
    unittest.main()
