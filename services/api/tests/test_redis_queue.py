"""Tests for the durable Redis ingestion queue + worker (Phase 1 scaling)."""

from __future__ import annotations

from pathlib import Path

import pytest

fakeredis = pytest.importorskip("fakeredis")

from omni_modal.ingestion.models import (
    IngestionErrorCode,
    IngestionRequest,
    IngestionResult,
)
from omni_modal.ingestion.redis_queue import (
    DEADLETTER_KEY,
    QUEUE_KEY,
    RedisIngestionQueue,
    RedisIngestionWorker,
    select_ingestion_queue,
)


@pytest.fixture()
def client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake content")
    return f


def _request(sample_file: Path) -> IngestionRequest:
    return IngestionRequest(
        tenant_id="t1",
        document_id="11111111-1111-1111-1111-111111111111",
        owner_id="u1",
        file_path=sample_file,
        source_kind="pdf",
        title="Doc",
    )


class _OkPipeline:
    def __init__(self):
        self.calls = 0

    def ingest(self, request: IngestionRequest) -> IngestionResult:
        self.calls += 1
        return IngestionResult(
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            owner_id=request.owner_id,
            title=request.title or "Doc",
            source_kind=request.source_kind,
            status="ready",
            chunks=[],
            metadata={"chunk_count": 0},
        )


class _FailingPipeline:
    def __init__(self):
        self.calls = 0

    def ingest(self, request: IngestionRequest) -> IngestionResult:
        self.calls += 1
        raise RuntimeError("boom")


def test_enqueue_persists_job_and_payload(client, sample_file):
    queue = RedisIngestionQueue(client, _OkPipeline())
    job = queue.enqueue(_request(sample_file))
    assert job.status == "uploaded"
    # Job is durable in Redis and queued for the worker.
    assert client.exists(f"omero:ingest:job:{job.id}")
    assert client.llen(QUEUE_KEY) == 1
    assert client.exists(f"omero:ingest:payload:{job.id}")


def test_get_round_trips_job(client, sample_file):
    queue = RedisIngestionQueue(client, _OkPipeline())
    job = queue.enqueue(_request(sample_file))
    fetched = queue.get(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.request.tenant_id == "t1"
    assert fetched.request.document_id == job.request.document_id


def test_worker_processes_job_to_ready(client, sample_file):
    pipeline = _OkPipeline()
    queue = RedisIngestionQueue(client, pipeline)
    job = queue.enqueue(_request(sample_file))

    worker = RedisIngestionWorker(client, pipeline)
    processed = worker.drain()
    assert processed == 1
    assert pipeline.calls == 1

    done = queue.get(job.id)
    assert done is not None
    assert done.status == "ready"
    assert done.result is not None
    assert done.result.document_id == job.request.document_id
    # Payload cleaned up after terminal state.
    assert not client.exists(f"omero:ingest:payload:{job.id}")


def test_cache_evict_callback_invoked_on_success(client, sample_file):
    evicted: list[str] = []
    pipeline = _OkPipeline()
    queue = RedisIngestionQueue(client, pipeline)
    job = queue.enqueue(_request(sample_file))

    worker = RedisIngestionWorker(client, pipeline, cache_evict_callback=evicted.append)
    worker.drain()
    assert evicted == ["t1"]


def test_failure_retries_then_dead_letters(client, sample_file):
    pipeline = _FailingPipeline()
    queue = RedisIngestionQueue(client, pipeline, max_retries=2)
    job = queue.enqueue(_request(sample_file))

    worker = RedisIngestionWorker(client, pipeline, max_retries=2)
    worker.drain()

    # 2 attempts total (initial + 1 retry), then dead-lettered.
    assert pipeline.calls == 2
    final = queue.get(job.id)
    assert final is not None
    assert final.status == "failed"
    assert final.error_code == IngestionErrorCode.EXTRACTION_FAILED
    assert client.lrem(DEADLETTER_KEY, 0, job.id) == 1


def test_explicit_fail(client, sample_file):
    queue = RedisIngestionQueue(client, _OkPipeline())
    job = queue.enqueue(_request(sample_file))
    failed = queue.fail(job.id, IngestionErrorCode.EMPTY_TEXT, "no text")
    assert failed.status == "failed"
    assert failed.error_code == IngestionErrorCode.EMPTY_TEXT
    assert failed.error_message == "no text"


def test_get_missing_returns_none(client):
    queue = RedisIngestionQueue(client, _OkPipeline())
    assert queue.get("nonexistent") is None


def test_select_ingestion_queue_prefers_redis(client):
    from omni_modal.cache import redis_client

    redis_client.set_test_client(client)
    try:
        queue = select_ingestion_queue(_OkPipeline())
        assert isinstance(queue, RedisIngestionQueue)
    finally:
        redis_client.set_test_client(None)


def test_select_ingestion_queue_falls_back(monkeypatch):
    from omni_modal.cache import redis_client
    from omni_modal.ingestion.async_queue import AsyncIngestionQueue

    redis_client.set_test_client(None)
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_client.reset_for_testing()
    queue = select_ingestion_queue(_OkPipeline())
    assert isinstance(queue, AsyncIngestionQueue)
