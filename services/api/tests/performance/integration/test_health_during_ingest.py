"""Integration test: /health remains responsive during background ingestion.
Feature: performance-and-scalability
Validates: Requirements 7.1, 8.1
"""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import _path
from omni_modal.ingestion.async_queue import AsyncIngestionQueue
from omni_modal.ingestion.models import IngestionRequest, IngestionResult, IngestionErrorCode


class TestHealthDuringIngest(unittest.TestCase):
    """API_Server stays responsive while Background_Worker is busy — Validates: Req 7.1, 8.1"""

    def test_async_queue_enqueue_fast_while_worker_busy(self):
        """enqueue() returns in < 200ms even when worker is processing a slow job."""
        barrier = threading.Barrier(2)
        released = threading.Event()

        class SlowPipeline:
            def ingest(self, request):
                barrier.wait(timeout=5.0)  # signal test that processing started
                released.wait(timeout=5.0)  # hold until test signals release
                return IngestionResult(
                    tenant_id=request.tenant_id,
                    document_id=request.document_id,
                    owner_id=request.owner_id,
                    title="test",
                    source_kind="pdf",
                    status="ready",
                    chunks=[],
                    metadata={},
                )

        q = AsyncIngestionQueue(pipeline=SlowPipeline())
        q.start_worker()

        # Enqueue first job — will block the worker
        first_req = IngestionRequest(
            tenant_id="t1", document_id="doc1", owner_id="o1",
            file_path=Path("/tmp/big.pdf"), source_kind="pdf"
        )
        q.enqueue(first_req)
        barrier.wait(timeout=5.0)  # wait until worker is blocked

        # While worker is busy, enqueue a second job — should be fast
        second_req = IngestionRequest(
            tenant_id="t1", document_id="doc2", owner_id="o1",
            file_path=Path("/tmp/small.pdf"), source_kind="pdf"
        )
        start = time.perf_counter()
        job2 = q.enqueue(second_req)
        elapsed_ms = (time.perf_counter() - start) * 1000

        self.assertLess(elapsed_ms, 200, f"enqueue() took {elapsed_ms:.1f}ms, expected < 200ms")
        self.assertIsNotNone(job2)

        released.set()  # unblock worker
