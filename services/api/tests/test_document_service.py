"""DocumentService: durable uploads, stages, idempotency, delete, reprocess, resume.

Runs with the real pipeline, in-memory stores and local storage; only PDF text
extraction is faked (the files here are tiny fake PDFs).
"""

from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

import _path  # noqa: F401

from omni_modal.documents.service import (
    DocumentService,
    Forbidden,
    InvalidState,
    UploadRejected,
)
from omni_modal.documents.store import DocumentNotFound, InMemoryDocumentStore
from omni_modal.ingestion.models import ExtractedTextSegment, SourceReference
from omni_modal.ingestion.pipeline import MultimodalIngestionPipeline
from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider
from omni_modal.qa.in_memory_store import InMemoryChunkPersistence, InMemoryVectorStore
from omni_modal.saas.adapters.storage import LocalStorageAdapter


class _TextPdfExtractor:
    """Treats everything after the %PDF header as page text, one page per '|'."""

    def extract(self, file_path: Path) -> list[ExtractedTextSegment]:
        text = file_path.read_bytes().decode("utf-8", "replace").split("\n", 1)[-1]
        return [
            ExtractedTextSegment(text=page, reference=SourceReference(str(file_path), "pdf", page_number=i))
            for i, page in enumerate(text.split("|"), start=1) if page.strip()
        ]


class _BrokenPersistence:
    def __init__(self) -> None:
        self.calls = 0

    def persist(self, result) -> int:
        self.calls += 1
        raise ConnectionError("database went away")


def _pdf(text: str) -> bytes:
    return b"%PDF-1.4\n" + text.encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Entities:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.extracted: list[str] = []

    def entity_records_from_result(self, result):
        self.extracted.append(result.document_id)
        return []

    def delete_records(self, tenant_id, document_id):
        self.deleted.append((tenant_id, document_id))


class DocumentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = LocalStorageAdapter(self.tmp.name)
        self.vectors = InMemoryVectorStore()
        self.store = InMemoryDocumentStore()
        self.entities = _Entities()
        self.evicted: list[str] = []
        self.dispatched: list[tuple[str, str]] = []
        self.service = self._service()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _service(self, persistence=None, **kw) -> DocumentService:
        provider = HashingQueryEmbeddingProvider()
        pipeline = MultimodalIngestionPipeline(
            pdf_extractor=_TextPdfExtractor(),
            persistence=persistence or InMemoryChunkPersistence(self.vectors, provider),
        )
        return DocumentService(
            store=self.store, storage=self.storage, pipeline=pipeline,
            remove_vectors=self.vectors.remove_document, entity_service=self.entities,
            evict_cache=self.evicted.append, dispatch=lambda t, j: self.dispatched.append((t, j)), **kw,
        )

    def _age_job(self, job_id: str, *, seconds: float) -> None:
        job = self.store._jobs[job_id]
        self.store._jobs[job_id] = replace(job, created_at=job.created_at - seconds,
                                           updated_at=job.updated_at - seconds)

    def _run_dispatched(self, service=None) -> None:
        service = service or self.service
        while self.dispatched:
            tenant, job_id = self.dispatched.pop(0)
            service.run_job(tenant, job_id)

    def _upload(self, data: bytes, tenant="t1", user="u1", name="minutes.pdf"):
        slot = self.service.create_upload(
            tenant, user, file_name=name, size_bytes=len(data), content_type="application/pdf", sha256=_sha(data),
        )
        if not slot.duplicate:
            self.service.receive_content(tenant, user, slot.document.id, [data])
            self.service.complete_upload(tenant, user, slot.document.id)
            self._run_dispatched()
        return slot

    # ── Happy path ───────────────────────────────────────────────────────
    def test_document_exists_from_the_first_click_and_reaches_ready(self) -> None:
        data = _pdf("Revenue grew 18 percent.|Headcount rose to 145.")
        slot = self.service.create_upload(
            "t1", "u1", file_name="minutes.pdf", size_bytes=len(data), content_type="application/pdf", sha256=_sha(data),
        )
        doc, job = self.service.get("t1", slot.document.id)
        self.assertEqual((doc.status, job.stage), ("uploaded", "awaiting_upload"))
        self.assertIsNone(slot.upload)  # local storage: the API's own route is used

        self.service.receive_content("t1", "u1", doc.id, [data[:5], data[5:]])
        self.service.complete_upload("t1", "u1", doc.id)
        self.assertEqual(self.service.get("t1", doc.id)[1].stage, "queued")
        self._run_dispatched()

        doc, job = self.service.get("t1", doc.id)
        self.assertEqual((doc.status, job.stage, job.attempts), ("ready", "done", 1))
        self.assertEqual(doc.chunk_count, len(self.vectors.for_tenant("t1")))
        self.assertGreater(doc.chunk_count, 0)
        self.assertEqual(doc.size_bytes, len(data))
        self.assertTrue(self.vectors.for_tenant("t1"))
        self.assertIn("t1", self.evicted)
        self.assertEqual(self.entities.extracted, [doc.id])

    def test_stages_are_recorded_in_order(self) -> None:
        stages: list[str] = []
        original = self.store.update_job

        def spy(tenant_id, job_id, **fields):
            if "stage" in fields:
                stages.append(fields["stage"])
            return original(tenant_id, job_id, **fields)

        self.store.update_job = spy
        self._upload(_pdf("Revenue grew."))
        self.assertEqual(stages, ["queued", "extracting", "chunking", "embedding", "done"])

    # ── Idempotency ──────────────────────────────────────────────────────
    def test_same_file_twice_returns_the_existing_document(self) -> None:
        data = _pdf("Revenue grew.")
        first = self._upload(data)
        second = self._upload(data, name="copy-of-minutes.pdf")
        self.assertTrue(second.duplicate)
        self.assertEqual(second.document.id, first.document.id)
        self.assertEqual(len(self.service.list("t1")), 1)

    def test_same_file_in_another_tenant_is_a_separate_document(self) -> None:
        data = _pdf("Revenue grew.")
        a = self._upload(data, tenant="t1")
        b = self._upload(data, tenant="t2")
        self.assertFalse(b.duplicate)
        self.assertNotEqual(a.document.id, b.document.id)

    # ── Validation ───────────────────────────────────────────────────────
    def test_rejects_unsupported_type_bad_hash_and_oversize(self) -> None:
        with self.assertRaises(UploadRejected):
            self.service.create_upload("t1", "u1", file_name="notes.exe", size_bytes=10,
                                       content_type="application/octet-stream", sha256="a" * 64)
        with self.assertRaises(UploadRejected):
            self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=10,
                                       content_type="application/pdf", sha256="not-a-hash")
        with self.assertRaises(UploadRejected):
            self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=10**10,
                                       content_type="application/pdf", sha256="a" * 64)

    def test_local_upload_stops_reading_past_the_declared_size(self) -> None:
        data = _pdf("x")
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        read: list[int] = []

        def endless():
            while True:
                read.append(1)
                yield b"0" * 1024

        with self.assertRaises(UploadRejected):
            self.service.receive_content("t1", "u1", slot.document.id, endless())
        self.assertLess(len(read), 10)
        self.assertIsNone(self.storage.head(f"tenants/t1/documents/{slot.document.id}/original.pdf"))

    def test_complete_without_the_file_is_refused(self) -> None:
        data = _pdf("x")
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        with self.assertRaises(InvalidState):
            self.service.complete_upload("t1", "u1", slot.document.id)

    def test_changed_bytes_fail_the_hash_check(self) -> None:
        data = _pdf("Revenue grew.")
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        tampered = data.replace(b"grew", b"fell")
        self.service.receive_content("t1", "u1", slot.document.id, [tampered])
        self.service.complete_upload("t1", "u1", slot.document.id)
        self._run_dispatched()
        doc, job = self.service.get("t1", slot.document.id)
        self.assertEqual((doc.status, job.stage), ("failed", "failed"))
        self.assertIn("did not match", doc.error_message)
        self.assertEqual(self.vectors.for_tenant("t1"), [])

    def test_unreadable_file_fails_with_the_pipeline_message(self) -> None:
        slot = self._upload(_pdf("   "))
        doc, job = self.service.get("t1", slot.document.id)
        self.assertEqual(job.stage, "failed")
        self.assertEqual(doc.error_message, "No text was extracted from the uploaded file.")

    # ── Isolation and permissions ────────────────────────────────────────
    def test_other_tenant_sees_nothing(self) -> None:
        slot = self._upload(_pdf("Revenue grew."))
        for call in (
            lambda: self.service.get("t2", slot.document.id),
            lambda: self.service.download("t2", slot.document.id),
            lambda: self.service.delete("t2", "u1", ("admin",), slot.document.id),
            lambda: self.service.reprocess("t2", "u1", ("admin",), slot.document.id),
            lambda: self.service.receive_content("t2", "u1", slot.document.id, [b"x"]),
        ):
            with self.assertRaises(DocumentNotFound):
                call()
        self.assertEqual(self.service.list("t2"), [])

    def test_only_uploader_or_admin_may_delete_or_reprocess(self) -> None:
        slot = self._upload(_pdf("Revenue grew."))
        with self.assertRaises(Forbidden):
            self.service.delete("t1", "u2", ("researcher",), slot.document.id)
        with self.assertRaises(Forbidden):
            self.service.reprocess("t1", "u2", ("researcher",), slot.document.id)
        self.service.reprocess("t1", "u2", ("admin",), slot.document.id)

    def test_only_the_uploader_may_send_the_bytes(self) -> None:
        data = _pdf("x")
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        with self.assertRaises(Forbidden):
            self.service.receive_content("t1", "u2", slot.document.id, [data])

    # ── Delete, reprocess, download ──────────────────────────────────────
    def test_delete_removes_object_chunks_entities_and_cache(self) -> None:
        slot = self._upload(_pdf("Revenue grew."))
        key = f"tenants/t1/documents/{slot.document.id}/original.pdf"
        self.assertIsNotNone(self.storage.head(key))
        self.evicted.clear()
        self.service.delete("t1", "u1", ("researcher",), slot.document.id)
        self.assertIsNone(self.storage.head(key))
        self.assertEqual(self.vectors.for_tenant("t1"), [])
        self.assertIn(("t1", slot.document.id), self.entities.deleted)
        self.assertEqual(self.evicted, ["t1"])
        with self.assertRaises(DocumentNotFound):
            self.service.get("t1", slot.document.id)
        # The same file can be uploaded again afterwards.
        self.assertFalse(self._upload(_pdf("Revenue grew.")).duplicate)

    def test_reprocess_replaces_chunks_with_a_new_job(self) -> None:
        slot = self._upload(_pdf("Revenue grew.|Headcount rose."))
        before = len(self.vectors.for_tenant("t1"))
        job = self.service.reprocess("t1", "u1", ("researcher",), slot.document.id)
        self.assertEqual(job.stage, "queued")
        self._run_dispatched()
        self.assertEqual(len(self.vectors.for_tenant("t1")), before)
        self.assertEqual(self.service.get("t1", slot.document.id)[1].id, job.id)

    def test_reprocess_refused_while_a_job_is_running(self) -> None:
        data = _pdf("x")
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        self.service.receive_content("t1", "u1", slot.document.id, [data])
        self.service.complete_upload("t1", "u1", slot.document.id)
        with self.assertRaises(InvalidState):
            self.service.reprocess("t1", "u1", ("researcher",), slot.document.id)

    def test_download_for_local_storage_is_served_by_the_api(self) -> None:
        slot = self._upload(_pdf("Revenue grew."))
        url, doc = self.service.download("t1", slot.document.id)
        self.assertIsNone(url)
        self.assertEqual(self.service.local_path("t1", doc.id).read_bytes(), _pdf("Revenue grew."))

    # ── Failures, retries and restarts ───────────────────────────────────
    def test_transient_failure_is_retried_then_gives_up(self) -> None:
        broken = _BrokenPersistence()
        service = self._service(persistence=broken, max_attempts=3)
        data = _pdf("Revenue grew.")
        slot = service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                     content_type="application/pdf", sha256=_sha(data))
        service.receive_content("t1", "u1", slot.document.id, [data])
        service.complete_upload("t1", "u1", slot.document.id)
        self._run_dispatched(service)
        doc, job = service.get("t1", slot.document.id)
        self.assertEqual(broken.calls, 3)
        self.assertEqual((job.stage, job.attempts, doc.status), ("failed", 3, "failed"))
        # The user sees a plain message; the technical cause goes to monitoring only.
        self.assertNotIn("database went away", doc.error_message)
        self.assertIn("temporary problem", doc.error_message)

    def test_restart_resumes_unfinished_jobs(self) -> None:
        data = _pdf("Revenue grew.")
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        self.service.receive_content("t1", "u1", slot.document.id, [data])
        self.service.complete_upload("t1", "u1", slot.document.id)
        job = self.service.get("t1", slot.document.id)[1]
        self.store.update_job("t1", job.id, stage="chunking", attempts=1)  # died mid-job
        self._age_job(job.id, seconds=3600)
        self.dispatched.clear()

        restarted = self._service()  # new process, same durable store and storage
        restarted.resume_unfinished()
        self.assertEqual(self.dispatched, [("t1", job.id)])
        self._run_dispatched(restarted)
        self.assertEqual(restarted.get("t1", slot.document.id)[1].stage, "done")

    def test_restart_gives_up_on_a_job_that_keeps_dying(self) -> None:
        data = _pdf("Revenue grew.")
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        self.service.receive_content("t1", "u1", slot.document.id, [data])
        self.service.complete_upload("t1", "u1", slot.document.id)
        job = self.service.get("t1", slot.document.id)[1]
        self.store.update_job("t1", job.id, stage="embedding", attempts=3)
        self._age_job(job.id, seconds=3600)
        self.dispatched.clear()
        self._service(max_attempts=3).resume_unfinished()
        self.assertEqual(self.dispatched, [])
        doc, job = self.service.get("t1", slot.document.id)
        self.assertEqual((job.stage, doc.status), ("failed", "failed"))

    def test_restart_removes_upload_slots_abandoned_for_a_day(self) -> None:
        data = _pdf("x")
        old = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                         content_type="application/pdf", sha256=_sha(data))
        fresh_data = _pdf("y")
        fresh = self.service.create_upload("t1", "u1", file_name="b.pdf", size_bytes=len(fresh_data),
                                           content_type="application/pdf", sha256=_sha(fresh_data))
        self._age_job(self.service.get("t1", old.document.id)[1].id, seconds=2 * 86400)
        self._service().resume_unfinished()
        with self.assertRaises(DocumentNotFound):
            self.service.get("t1", old.document.id)
        self.service.get("t1", fresh.document.id)

    # ── Review fixes ─────────────────────────────────────────────────────
    def _queued(self, data: bytes):
        slot = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                          content_type="application/pdf", sha256=_sha(data))
        self.service.receive_content("t1", "u1", slot.document.id, [data])
        self.service.complete_upload("t1", "u1", slot.document.id)
        return slot, self.service.get("t1", slot.document.id)[1]

    def test_a_job_runs_once_even_when_dispatched_twice(self) -> None:
        calls = []
        original = self.service._pipeline.ingest
        self.service._pipeline.ingest = lambda *a, **k: calls.append(1) or original(*a, **k)
        _, job = self._queued(_pdf("Revenue grew."))
        self.dispatched.append(("t1", job.id))  # e.g. the sweeper re-sent it
        self._run_dispatched()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.service.get_job("t1", job.id).attempts, 1)

    def test_re_adding_a_file_whose_upload_broke_resumes_it(self) -> None:
        data = _pdf("Revenue grew.")
        first = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                           content_type="application/pdf", sha256=_sha(data))
        again = self.service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                           content_type="application/pdf", sha256=_sha(data))
        self.assertFalse(again.duplicate)
        self.assertTrue(again.resumed)
        self.assertEqual(again.document.id, first.document.id)
        # Someone else in the tenant sees the existing document instead.
        other = self.service.create_upload("t1", "u2", file_name="a.pdf", size_bytes=len(data),
                                           content_type="application/pdf", sha256=_sha(data))
        self.assertTrue(other.duplicate)

    def test_delete_during_processing_does_not_bring_the_document_back(self) -> None:
        slot, _job = self._queued(_pdf("Secret plan."))
        original = self.service._pipeline.ingest

        def ingest_then_deleted(request, on_stage=None):
            result = original(request, on_stage=on_stage)
            self.service.delete("t1", "u1", ("researcher",), slot.document.id)
            return result

        self.service._pipeline.ingest = ingest_then_deleted
        self._run_dispatched()
        self.assertEqual(self.vectors.for_tenant("t1"), [])
        self.assertEqual(self.service.list("t1"), [])

    def test_failed_reprocess_does_not_keep_old_answers(self) -> None:
        slot = self._upload(_pdf("Revenue grew."))
        self.assertTrue(self.vectors.for_tenant("t1"))

        class _Empty:
            def extract(self, path):
                return []

        self.service._pipeline._pdf_extractor = _Empty()
        self.service.reprocess("t1", "u1", ("researcher",), slot.document.id)
        self._run_dispatched()
        self.assertEqual(self.service.get("t1", slot.document.id)[0].status, "failed")
        self.assertEqual(self.vectors.for_tenant("t1"), [])

    def test_bytes_cannot_be_swapped_after_completion(self) -> None:
        slot, _ = self._queued(_pdf("Original text."))
        key = slot.document.storage_key
        self.assertIsNone(self.storage.head(f"{key}.incoming"))  # moved into place on completion
        self.assertIsNotNone(self.storage.head(key))

    def test_sweeper_redispatches_a_lost_queued_job_and_retries_use_backoff(self) -> None:
        later: list[float] = []
        service = self._service(persistence=_BrokenPersistence(), max_attempts=3)
        service.set_dispatch(lambda t, j: self.dispatched.append((t, j)),
                             lambda t, j, delay: later.append(delay))
        data = _pdf("x")
        slot = service.create_upload("t1", "u1", file_name="a.pdf", size_bytes=len(data),
                                     content_type="application/pdf", sha256=_sha(data))
        service.receive_content("t1", "u1", slot.document.id, [data])
        service.complete_upload("t1", "u1", slot.document.id)
        job_id = self.dispatched.pop()[1]
        service.run_job("t1", job_id)
        self.assertEqual(later, [5.0])  # retried after a pause, not at once
        self.assertEqual(self.dispatched, [])
        self._age_job(job_id, seconds=600)
        service.sweep()
        self.assertEqual(self.dispatched, [("t1", job_id)])

    def test_rules_are_checked_before_anything_is_created(self) -> None:
        with self.assertRaises(UploadRejected):
            self.service.validate_upload(file_name="a.exe", size_bytes=1, sha256="a" * 64)
        self.assertEqual(self.service.list("t1"), [])

    def test_ingest_bytes_runs_the_whole_flow_in_one_call(self) -> None:
        data = _pdf("Revenue grew.")
        doc, job, duplicate = self.service.ingest_bytes("t1", "u1", file_name="a.pdf", data=data, title="Minutes")
        self.assertFalse(duplicate)
        self.assertEqual(doc.title, "Minutes")
        self._run_dispatched()
        self.assertEqual(self.service.get("t1", doc.id)[0].status, "ready")
        _, again, duplicate = self.service.ingest_bytes("t1", "u1", file_name="a.pdf", data=data)
        self.assertTrue(duplicate)
        self.assertIsNone(again)


if __name__ == "__main__":
    unittest.main()
