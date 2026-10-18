"""Runs ingestion jobs on a background thread inside the API process.

The deployment target (Render free tier) has no separate worker service, so
jobs run here. Only job ids travel through this queue; the job's state lives
in the document store, and claiming a job is atomic, so a job is never run
twice at once and nothing is lost when the process stops.

A second thread calls ``sweep`` every ``sweep_interval`` seconds, which
recovers jobs whose worker vanished and clears abandoned upload slots.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from omni_modal.observability import observability

logger = logging.getLogger(__name__)


class InProcessDispatcher:
    def __init__(
        self,
        run: Callable[[str, str], None],
        *,
        sweep: Callable[[], None] | None = None,
        sweep_interval: float = 60.0,
    ) -> None:
        self._run = run
        self._sweep = sweep
        self._sweep_interval = sweep_interval
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._timers: set[threading.Timer] = set()
        self._timers_lock = threading.Lock()

    def __call__(self, tenant_id: str, job_id: str) -> None:
        self._queue.put((tenant_id, job_id))

    def later(self, tenant_id: str, job_id: str, delay: float) -> None:
        """Queue a job after ``delay`` seconds (retry backoff)."""
        def fire() -> None:
            with self._timers_lock:
                self._timers.discard(timer)
            if not self._stop.is_set():
                self(tenant_id, job_id)

        timer = threading.Timer(delay, fire)
        timer.daemon = True
        with self._timers_lock:
            self._timers.add(timer)
        timer.start()

    def start(self) -> None:
        if any(t.is_alive() for t in self._threads):
            return
        self._stop.clear()
        self._threads = [threading.Thread(target=self._loop, daemon=True, name="IngestionDispatcher")]
        if self._sweep is not None:
            self._threads.append(threading.Thread(target=self._sweep_loop, daemon=True, name="IngestionSweeper"))
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._timers_lock:
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
        for thread in self._threads:
            thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def wait_idle(self) -> None:
        """Block until every submitted job has run (tests and scripts)."""
        self._queue.join()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                tenant_id, job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._run(tenant_id, job_id)
            except Exception as exc:  # noqa: BLE001 - one bad job must not stop the worker
                logger.exception("Ingestion job %s crashed: %s", job_id, exc)
                observability.capture_exception(exc, operation="documents.dispatch", context={"job_id": job_id})
            finally:
                self._queue.task_done()

    def _sweep_loop(self) -> None:
        while not self._stop.wait(self._sweep_interval):
            try:
                self._sweep()
            except Exception as exc:  # noqa: BLE001 - a failed sweep is retried next interval
                observability.capture_exception(exc, operation="documents.sweep")
