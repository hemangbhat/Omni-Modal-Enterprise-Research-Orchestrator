"""The upload-to-ready flow, durable at every step.

    create_upload  -> document row + job (awaiting_upload); where to send bytes
    bytes arrive   -> under a temporary "incoming" key (S3 form upload, or the API route)
    complete_upload -> size checked, moved to the final key, job queued and dispatched
    run_job        -> claimed atomically, then extracting|transcribing -> chunking
                      -> embedding -> done

The document is marked ready only after the pipeline has committed its chunks
and embeddings. A system failure (storage, database, network) is retried with
backoff up to ``max_attempts``; a problem with the file itself (unreadable, no
text, changed bytes) fails at once with a plain reason. ``sweep`` runs in the
background: it requeues jobs whose worker vanished (no progress within the
lease), retries queued jobs that were never picked up, and clears upload slots
abandoned for a day.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from omni_modal.documents.store import (
    ACTIVE_STAGES,
    SORTS,
    DocumentQuery,
    DocumentNotFound,
    DocumentRecord,
    DuplicateDocument,
    JobRecord,
    document_status_for,
)
from omni_modal.ingestion.models import IngestionRequest
from omni_modal.ingestion.pipeline import AUDIO_SUFFIXES, PDF_SUFFIXES
from omni_modal.observability import observability
from omni_modal.security.upload_safety import MAX_FILE_BYTES

ABANDONED_UPLOAD_SECONDS = 24 * 3600
# A job that has shown no progress for this long is presumed orphaned (its
# worker died) and is requeued. Every stage change counts as progress.
JOB_LEASE_SECONDS = 10 * 60
# Queued jobs older than this are dispatched again (a lost queue entry).
REDISPATCH_AFTER_SECONDS = 2 * 60
RETRY_BACKOFF_SECONDS = 5.0
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Pipeline failures caused by the system (database, network), not the file:
# these are retried. Anything else is a problem with the file and fails at once.
_INFRASTRUCTURE_FAILURES = ("Persistence failed", "Unexpected ingestion failure")
_SYSTEM_FAILURE_MESSAGE = (
    "Processing stopped because of a temporary problem on our side. Use Reprocess to try again."
)


class UploadRejected(ValueError):
    """The request or the file breaks a rule the user can fix (type, size, hash)."""


class InvalidState(Exception):
    """The action does not fit the document's current stage."""


class Forbidden(Exception):
    pass


@dataclass(frozen=True)
class UploadSlot:
    document: DocumentRecord
    duplicate: bool
    # How the browser sends the bytes when object storage is configured: a form
    # POST ({"url", "fields"}) whose policy accepts exactly the declared size.
    # None means the API's own authenticated upload route must be used.
    upload: dict | None = None
    # True when this re-opens an unfinished upload of the same file.
    resumed: bool = False


def _source_kind(file_name: str) -> str | None:
    suffix = Path(file_name).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return None


def _clean_name(file_name: str) -> str:
    name = Path(file_name.replace("\\", "/")).name.strip()
    return name[:200] or "upload"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def incoming_key(storage_key: str) -> str:
    """Where uploaded bytes wait until the upload is confirmed."""
    return f"{storage_key}.incoming"


class DocumentService:
    def __init__(
        self,
        *,
        store,
        storage,
        pipeline,
        remove_vectors: Callable[[str, str], object] | None = None,
        entity_service=None,
        evict_cache: Callable[[str], object] | None = None,
        dispatch: Callable[[str, str], None] | None = None,
        dispatch_later: Callable[[str, str, float], None] | None = None,
        notify: Callable[[DocumentRecord, str], None] | None = None,
        max_bytes: int = MAX_FILE_BYTES,
        max_attempts: int = 3,
    ) -> None:
        self._store = store
        self._storage = storage
        self._pipeline = pipeline
        self._remove_vectors = remove_vectors
        self._entities = entity_service
        self._evict = evict_cache
        self._dispatch = dispatch or (lambda tenant_id, job_id: self.run_job(tenant_id, job_id))
        self._dispatch_later = dispatch_later
        self._notify = notify
        self._max_bytes = max_bytes
        self._max_attempts = max_attempts

    def set_dispatch(self, dispatch: Callable[[str, str], None],
                     dispatch_later: Callable[[str, str, float], None] | None = None) -> None:
        self._dispatch = dispatch
        self._dispatch_later = dispatch_later

    @staticmethod
    def storage_key(tenant_id: str, document_id: str, file_name: str) -> str:
        return f"tenants/{tenant_id}/documents/{document_id}/original{Path(file_name).suffix.lower()}"

    # ── Upload ───────────────────────────────────────────────────────────
    def validate_upload(self, *, file_name: str, size_bytes: int, sha256: str) -> tuple[str, str, str]:
        """Check an upload request before anything is created or charged.

        Returns the cleaned file name, the source kind and the normalised hash.
        """
        file_name = _clean_name(file_name)
        kind = _source_kind(file_name)
        if kind is None:
            raise UploadRejected("Only PDF and audio files (mp3, wav, m4a, flac, ogg, webm) can be uploaded.")
        if size_bytes <= 0:
            raise UploadRejected("The file is empty.")
        if size_bytes > self._max_bytes:
            raise UploadRejected(f"The file is larger than the {self._max_bytes // (1024 * 1024)} MB limit.")
        sha256 = sha256.lower()
        if not _SHA256.match(sha256):
            raise UploadRejected("sha256 must be the 64-character hex SHA-256 of the file.")
        return file_name, kind, sha256

    def _upload_target(self, doc: DocumentRecord) -> dict | None:
        return self._storage.presigned_upload(incoming_key(doc.storage_key), doc.size_bytes, doc.content_type)

    def create_upload(
        self, tenant_id: str, user_id: str, *, file_name: str, size_bytes: int,
        content_type: str, sha256: str, title: str | None = None,
    ) -> UploadSlot:
        file_name, kind, sha256 = self.validate_upload(file_name=file_name, size_bytes=size_bytes, sha256=sha256)

        existing = self._store.find_by_hash(tenant_id, sha256)
        if existing is not None:
            return self._existing_slot(existing, user_id)

        document_id = str(uuid.uuid4())
        record = DocumentRecord(
            id=document_id, tenant_id=tenant_id, uploaded_by=user_id,
            title=(title or "").strip()[:300] or file_name, file_name=file_name, source_kind=kind,
            content_type=(content_type or "application/octet-stream")[:200], size_bytes=size_bytes,
            content_hash=sha256, storage_key=self.storage_key(tenant_id, document_id, file_name),
        )
        try:
            doc = self._store.create_document(record)
        except DuplicateDocument as dup:  # lost a race with an identical upload
            return self._existing_slot(dup.existing, user_id)
        self._store.create_job(tenant_id, doc.id, "awaiting_upload")
        return UploadSlot(doc, duplicate=False, upload=self._upload_target(doc))

    def _existing_slot(self, existing: DocumentRecord, user_id: str) -> UploadSlot:
        """Same bytes already known: reopen the uploader's own unfinished upload,
        otherwise point at the existing document."""
        job = self._store.latest_job(existing.tenant_id, existing.id)
        if job is not None and job.stage == "awaiting_upload" and existing.uploaded_by == user_id:
            return UploadSlot(existing, duplicate=False, upload=self._upload_target(existing), resumed=True)
        return UploadSlot(existing, duplicate=True)

    def check_can_upload(self, tenant_id: str, user_id: str, document_id: str) -> DocumentRecord:
        """Who may send bytes to this slot: only its uploader, and only before completion."""
        doc = self._store.get_document(tenant_id, document_id)
        if doc.uploaded_by != user_id:
            raise Forbidden("Only the person who started this upload can send its file.")
        job = self._store.latest_job(tenant_id, document_id)
        if job is None or job.stage != "awaiting_upload":
            raise InvalidState("This upload has already been completed.")
        return doc

    def accept_file(self, tenant_id: str, user_id: str, document_id: str, path: Path) -> int:
        """Store a received file (already on local disk) as the slot's incoming bytes."""
        doc = self.check_can_upload(tenant_id, user_id, document_id)
        size = path.stat().st_size
        if size != doc.size_bytes:
            raise UploadRejected(f"Received {size} bytes but this upload declared {doc.size_bytes}.")
        self._storage.put_file(incoming_key(doc.storage_key), path)
        return size

    def receive_content(self, tenant_id: str, user_id: str, document_id: str, chunks: Iterable[bytes]) -> int:
        """Stream bytes to a temp file, stopping as soon as they pass the declared size."""
        doc = self.check_can_upload(tenant_id, user_id, document_id)
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".upload")
        path = Path(handle.name)
        try:
            received = 0
            with handle:
                for chunk in chunks:
                    received += len(chunk)
                    if received > doc.size_bytes:
                        raise UploadRejected("The file is larger than the size declared for this upload.")
                    handle.write(chunk)
            return self.accept_file(tenant_id, user_id, document_id, path)
        finally:
            path.unlink(missing_ok=True)

    def complete_upload(self, tenant_id: str, user_id: str, document_id: str) -> tuple[DocumentRecord, JobRecord]:
        doc = self.check_can_upload(tenant_id, user_id, document_id)
        job = self._store.latest_job(tenant_id, document_id)
        incoming = incoming_key(doc.storage_key)
        stored = self._storage.head(incoming)
        if stored is None:
            raise InvalidState("The file has not arrived in storage yet.")
        if stored != doc.size_bytes:
            self._storage.delete(incoming)
            raise UploadRejected(f"Stored file is {stored} bytes but {doc.size_bytes} were declared.")
        # From here the bytes cannot change: the upload link only ever wrote to "incoming".
        self._storage.move(incoming, doc.storage_key)
        job = self._store.update_job(tenant_id, job.id, stage="queued")
        self._dispatch(tenant_id, job.id)
        return doc, job

    def ingest_bytes(
        self, tenant_id: str, user_id: str, *, file_name: str, data: bytes,
        title: str | None = None, content_type: str = "application/octet-stream",
    ) -> tuple[DocumentRecord, JobRecord | None, bool]:
        """Server-side upload in one call (scripts, API clients, the base64 route)."""
        slot = self.create_upload(
            tenant_id, user_id, file_name=file_name, size_bytes=len(data), content_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(), title=title,
        )
        if slot.duplicate:
            return slot.document, None, True
        self.receive_content(tenant_id, user_id, slot.document.id, [data])
        doc, job = self.complete_upload(tenant_id, user_id, slot.document.id)
        return doc, job, False

    # ── Processing ───────────────────────────────────────────────────────
    def _set_stage(self, doc: DocumentRecord, job: JobRecord, stage: str) -> None:
        self._store.update_job(doc.tenant_id, job.id, stage=stage)
        self._store.update_document(doc.tenant_id, doc.id, status=document_status_for(stage))

    def _clear_index(self, tenant_id: str, document_id: str) -> None:
        if self._remove_vectors:
            try:
                self._remove_vectors(tenant_id, document_id)
            except Exception as exc:  # noqa: BLE001
                observability.capture_exception(exc, operation="documents.clear_index")

    def _fail(self, doc: DocumentRecord, job: JobRecord, message: str) -> None:
        """A final failure: the reason is shown to the user, so it must be plain."""
        self._store.update_job(doc.tenant_id, job.id, stage="failed", error_message=message)
        self._store.update_document(doc.tenant_id, doc.id, status="failed", error_message=message)
        # A document that failed must not keep answering from an older version.
        self._clear_index(doc.tenant_id, doc.id)
        if self._notify:
            self._notify(doc, "failed")

    def _exists(self, doc: DocumentRecord) -> bool:
        try:
            self._store.get_document(doc.tenant_id, doc.id)
            return True
        except DocumentNotFound:
            return False

    def run_job(self, tenant_id: str, job_id: str) -> None:
        job = self._store.claim_job(tenant_id, job_id)
        if job is None:
            return  # already running elsewhere, finished, or deleted
        try:
            doc = self._store.get_document(tenant_id, job.document_id)
        except DocumentNotFound:
            return
        tmp_path: Path | None = None
        try:
            if self._storage.head(doc.storage_key) is None:
                self._fail(doc, job, "The original file is missing from storage. Upload it again.")
                return
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(doc.file_name).suffix) as handle:
                tmp_path = Path(handle.name)
            self._storage.get_to_file(doc.storage_key, tmp_path)
            if _sha256_file(tmp_path) != doc.content_hash:
                self._fail(doc, job, "The stored file did not match the file that was announced. Upload it again.")
                return
            self._store.update_document(tenant_id, doc.id, error_message=None)
            result = self._pipeline.ingest(
                IngestionRequest(
                    tenant_id=tenant_id, document_id=doc.id, owner_id=doc.uploaded_by,
                    file_path=tmp_path, source_kind=doc.source_kind, title=doc.title,
                ),
                on_stage=lambda stage: self._set_stage(doc, job, stage),
            )
        except DocumentNotFound:
            self._clear_index(tenant_id, doc.id)  # deleted while processing
            return
        except Exception as exc:  # noqa: BLE001 - storage or database trouble: worth retrying
            self._retry_or_fail(doc, job, exc)
            return
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        if not self._exists(doc):
            # Deleted while the pipeline ran: undo anything it indexed.
            self._clear_index(tenant_id, doc.id)
            return

        if result.status != "ready":
            message = result.error_message or "Processing failed."
            if message.startswith(_INFRASTRUCTURE_FAILURES):
                self._retry_or_fail(doc, job, RuntimeError(message))
            else:
                # Pipeline messages describe the file; never leak the temp path.
                self._fail(doc, job, message.replace(str(tmp_path), doc.file_name))
            return

        self._store.update_job(tenant_id, job.id, stage="done")
        self._store.update_document(
            tenant_id, doc.id, status="ready", error_message=None,
            processed_at=time.time(), chunk_count=len(result.chunks),
        )
        if self._evict:
            self._evict(tenant_id)
        if self._entities is not None and self._exists(doc):
            try:
                self._entities.entity_records_from_result(result)
            except Exception as exc:  # noqa: BLE001 - entities must not fail the upload
                observability.capture_exception(exc, operation="documents.entities")
        if self._notify:
            self._notify(doc, "ready")

    def _retry_or_fail(self, doc: DocumentRecord, job: JobRecord, exc: Exception) -> None:
        observability.capture_exception(exc, operation="documents.run_job",
                                        context={"document_id": doc.id, "attempt": job.attempts})
        if job.attempts >= self._max_attempts:
            self._fail(doc, job, _SYSTEM_FAILURE_MESSAGE)
            return
        self._store.update_job(doc.tenant_id, job.id, stage="queued",
                               error_message=f"Attempt {job.attempts} hit a temporary problem; retrying.")
        delay = RETRY_BACKOFF_SECONDS * (2 ** (job.attempts - 1))
        if self._dispatch_later is not None:
            self._dispatch_later(doc.tenant_id, job.id, delay)
        else:
            self._dispatch(doc.tenant_id, job.id)

    def sweep(self, *, now: float | None = None) -> None:
        """Recover work that would otherwise be stuck. Safe to run repeatedly and
        from several processes: claiming a job is atomic."""
        now = time.time() if now is None else now
        for job in self._store.jobs_in_stages(ACTIVE_STAGES - {"queued"}):
            if now - job.updated_at < JOB_LEASE_SECONDS:
                continue  # still making progress somewhere
            try:
                doc = self._store.get_document(job.tenant_id, job.document_id)
            except DocumentNotFound:
                continue
            if job.attempts >= self._max_attempts:
                self._fail(doc, job, _SYSTEM_FAILURE_MESSAGE)
                continue
            self._store.update_job(job.tenant_id, job.id, stage="queued")
            self._dispatch(job.tenant_id, job.id)
        for job in self._store.jobs_in_stages({"queued"}):
            if now - job.updated_at >= REDISPATCH_AFTER_SECONDS:
                self._dispatch(job.tenant_id, job.id)
        for job in self._store.jobs_in_stages({"awaiting_upload"}):
            if now - job.created_at < ABANDONED_UPLOAD_SECONDS:
                continue
            try:
                doc = self._store.get_document(job.tenant_id, job.document_id)
                self._store.delete_document(job.tenant_id, job.document_id)
            except DocumentNotFound:
                continue
            for key in (doc.storage_key, incoming_key(doc.storage_key)):
                try:
                    self._storage.delete(key)
                except Exception as exc:  # noqa: BLE001
                    observability.capture_exception(exc, operation="documents.sweep_delete")

    def resume_unfinished(self) -> None:
        """At startup: the same recovery as the periodic sweep."""
        self.sweep()

    # ── Reads and actions ────────────────────────────────────────────────
    def find_duplicate(self, tenant_id: str, sha256: str) -> DocumentRecord | None:
        """The tenant's document with these exact bytes, if any (checked before charging quota)."""
        return self._store.find_by_hash(tenant_id, sha256.lower())

    def get(self, tenant_id: str, document_id: str) -> tuple[DocumentRecord, JobRecord | None]:
        doc = self._store.get_document(tenant_id, document_id)
        return doc, self._store.latest_job(tenant_id, document_id)

    def get_job(self, tenant_id: str, job_id: str) -> JobRecord:
        return self._store.get_job(tenant_id, job_id)

    def list(self, tenant_id: str) -> list[DocumentRecord]:
        return self._store.list_documents(tenant_id)

    def query(self, tenant_id: str, query: DocumentQuery) -> tuple[list[DocumentRecord], int]:
        """One page of the tenant's documents matching ``query``, and the total match count."""
        if query.sort not in SORTS:
            raise UploadRejected(f"sort must be one of: {', '.join(SORTS)}.")
        page_size = max(1, min(query.page_size, 200))
        page = max(1, query.page)
        return self._store.query_documents(
            tenant_id, DocumentQuery(**{**query.__dict__, "page": page, "page_size": page_size, "text": query.text[:200]})
        )

    def archive(self, tenant_id: str, user_id: str, roles, document_id: str) -> DocumentRecord:
        """Hide a document from research and the library until restored."""
        doc = self._store.get_document(tenant_id, document_id)
        self._may_change(doc, user_id, roles)
        updated = self._store.update_document(tenant_id, document_id, archived_at=time.time())
        if self._evict:
            self._evict(tenant_id)  # cached answers may cite it
        return updated

    def restore(self, tenant_id: str, user_id: str, roles, document_id: str) -> DocumentRecord:
        doc = self._store.get_document(tenant_id, document_id)
        self._may_change(doc, user_id, roles)
        updated = self._store.update_document(tenant_id, document_id, archived_at=None)
        if self._evict:
            self._evict(tenant_id)
        return updated

    def failures(self, tenant_id: str) -> list[tuple[DocumentRecord, JobRecord | None]]:
        """Failed documents with their last job, for the admin view."""
        docs, _ = self._store.query_documents(tenant_id, DocumentQuery(status="failed", page_size=200))
        return [(d, self._store.latest_job(tenant_id, d.id)) for d in docs]

    @staticmethod
    def _may_change(doc: DocumentRecord, user_id: str, roles) -> None:
        if doc.uploaded_by != user_id and "admin" not in roles:
            raise Forbidden("Only the person who uploaded this document, or an admin, can do that.")

    def delete(self, tenant_id: str, user_id: str, roles, document_id: str) -> None:
        doc = self._store.get_document(tenant_id, document_id)
        self._may_change(doc, user_id, roles)
        self._store.delete_document(tenant_id, document_id)  # Postgres cascades chunks, embeddings, jobs
        for key in (doc.storage_key, incoming_key(doc.storage_key)):
            try:
                self._storage.delete(key)
            except Exception as exc:  # noqa: BLE001 - the record is gone; an orphaned object is logged
                observability.capture_exception(exc, operation="documents.delete_object",
                                                context={"document_id": document_id})
        self._clear_index(tenant_id, document_id)
        if self._entities is not None and hasattr(self._entities, "delete_records"):
            self._entities.delete_records(tenant_id, document_id)
        if self._evict:
            self._evict(tenant_id)

    def reprocess(self, tenant_id: str, user_id: str, roles, document_id: str) -> JobRecord:
        doc = self._store.get_document(tenant_id, document_id)
        self._may_change(doc, user_id, roles)
        latest = self._store.latest_job(tenant_id, document_id)
        if latest is not None and latest.stage not in ("done", "failed"):
            raise InvalidState("This document is still being uploaded or processed.")
        if self._storage.head(doc.storage_key) is None:
            raise InvalidState("The original file is no longer in storage, so it cannot be reprocessed.")
        job = self._store.create_job(tenant_id, document_id, "queued")
        self._store.update_document(tenant_id, document_id, status="uploaded", error_message=None)
        self._dispatch(tenant_id, job.id)
        return job

    def download(self, tenant_id: str, document_id: str) -> tuple[str | None, DocumentRecord]:
        """A short-lived link to the original file, or None when the API must serve it."""
        doc = self._store.get_document(tenant_id, document_id)
        if self._storage.head(doc.storage_key) is None:
            raise InvalidState("The original file is not in storage.")
        return self._storage.presigned_get_url(doc.storage_key, doc.file_name), doc

    def local_path(self, tenant_id: str, document_id: str) -> Path:
        doc = self._store.get_document(tenant_id, document_id)
        path_for = getattr(self._storage, "path_for", None)
        if path_for is None:
            raise InvalidState("This file is served through a storage link; use the download link instead.")
        return path_for(doc.storage_key)
