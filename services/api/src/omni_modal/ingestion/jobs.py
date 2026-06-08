from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from omni_modal.ingestion.models import (
    IngestionErrorCode,
    IngestionJob,
    IngestionRequest,
)
from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline


class InMemoryIngestionQueue:
    def __init__(self, pipeline: MultimodalIngestionPipeline | None = None) -> None:
        self._pipeline = pipeline or MultimodalIngestionPipeline()
        self._jobs: dict[str, IngestionJob] = {}

    def enqueue(self, request: IngestionRequest) -> IngestionJob:
        job = IngestionJob(
            id=str(uuid4()),
            request=request,
            status="uploaded",
        )
        self._jobs[job.id] = job
        return job

    def process_next(self) -> IngestionJob | None:
        for job in self._jobs.values():
            if job.status == "uploaded":
                return self.process(job.id)
        return None

    def process(self, job_id: str) -> IngestionJob:
        job = self._jobs[job_id]
        self._jobs[job_id] = replace(job, status="processing")
        result = self._pipeline.ingest(job.request)
        completed = replace(
            self._jobs[job_id],
            status=result.status,
            result=result,
            error_code=result.error_code,
            error_message=result.error_message,
        )
        self._jobs[job_id] = completed
        return completed

    def fail(
        self,
        job_id: str,
        error_code: IngestionErrorCode,
        error_message: str,
    ) -> IngestionJob:
        failed = replace(
            self._jobs[job_id],
            status="failed",
            error_code=error_code,
            error_message=error_message,
        )
        self._jobs[job_id] = failed
        return failed

    def get(self, job_id: str) -> IngestionJob | None:
        return self._jobs.get(job_id)
