"""Document and ingestion-job records: the durable truth about every upload.

A document row exists from the moment an upload slot is issued, and each
ingestion job records the stage it reached, how many attempts it took and the
error that stopped it. Every read is filtered by tenant, so another tenant's
id behaves exactly like an unknown one.

Backed by Postgres (``documents`` plus the ``ingestion_jobs`` table from
migration 0012) when ``DATABASE_URL`` is set, so records survive a restart,
and by a thread-safe in-memory store otherwise.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace

# Stages a job moves through, in order. "done" and "failed" are final.
STAGES = (
    "awaiting_upload", "queued", "extracting", "transcribing", "chunking", "embedding", "done", "failed",
)
ACTIVE_STAGES = frozenset({"queued", "extracting", "transcribing", "chunking", "embedding"})
FINAL_STAGES = frozenset({"done", "failed"})

# What each job stage means for the document as a whole.
_DOCUMENT_STATUS = {
    "awaiting_upload": "uploaded", "queued": "uploaded",
    "extracting": "processing", "transcribing": "processing",
    "chunking": "processing", "embedding": "processing",
    "done": "ready", "failed": "failed",
}


def document_status_for(stage: str) -> str:
    return _DOCUMENT_STATUS[stage]


class DocumentNotFound(Exception):
    pass


class DuplicateDocument(Exception):
    """Another document in the tenant already has this content hash."""

    def __init__(self, existing: "DocumentRecord") -> None:
        super().__init__(existing.id)
        self.existing = existing


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    tenant_id: str
    uploaded_by: str
    title: str
    file_name: str
    source_kind: str
    content_type: str
    size_bytes: int
    content_hash: str
    storage_key: str
    status: str = "uploaded"
    error_message: str | None = None
    chunk_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    processed_at: float | None = None
    # Archived documents are left out of research and the default list.
    archived_at: float | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("storage_key")  # internal: never sent to clients
        data["document_id"] = self.id
        data["source_kind"] = self.source_kind
        return data


@dataclass(frozen=True)
class JobRecord:
    id: str
    tenant_id: str
    document_id: str
    stage: str
    attempts: int = 0
    error_message: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {**asdict(self), "job_id": self.id, "status": document_status_for(self.stage)}


@dataclass(frozen=True)
class DocumentQuery:
    """Filters, sort and page for the document library."""

    text: str = ""
    status: str | None = None          # uploaded | processing | ready | failed
    source_kind: str | None = None     # pdf | audio
    uploaded_by: str | None = None
    archived: bool = False             # False: live documents; True: the archive
    document_ids: frozenset[str] | None = None  # e.g. one project's documents
    sort: str = "newest"               # newest | oldest | title | size
    page: int = 1
    page_size: int = 25


SORTS = ("newest", "oldest", "title", "size")


def _matches(d: "DocumentRecord", q: DocumentQuery) -> bool:
    if (d.archived_at is not None) != q.archived:
        return False
    if q.status and d.status != q.status:
        return False
    if q.source_kind and d.source_kind != q.source_kind:
        return False
    if q.uploaded_by and d.uploaded_by != q.uploaded_by:
        return False
    if q.document_ids is not None and d.id not in q.document_ids:
        return False
    text = q.text.strip().lower()
    return not text or text in d.title.lower() or text in (d.file_name or "").lower()


_SORT_KEYS = {
    "newest": (lambda d: d.created_at, True),
    "oldest": (lambda d: d.created_at, False),
    "title": (lambda d: d.title.lower(), False),
    "size": (lambda d: d.size_bytes, True),
}


class InMemoryDocumentStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._docs: dict[str, DocumentRecord] = {}
        self._jobs: dict[str, JobRecord] = {}

    # ── Documents ─────────────────────────────────────────────────────────
    def create_document(self, record: DocumentRecord) -> DocumentRecord:
        with self._lock:
            existing = self.find_by_hash(record.tenant_id, record.content_hash)
            if existing is not None:
                raise DuplicateDocument(existing)
            now = time.time()
            saved = replace(record, created_at=now, updated_at=now)
            self._docs[saved.id] = saved
            return saved

    def get_document(self, tenant_id: str, document_id: str) -> DocumentRecord:
        with self._lock:
            doc = self._docs.get(document_id)
            if doc is None or doc.tenant_id != tenant_id:
                raise DocumentNotFound(document_id)
            return doc

    def find_by_hash(self, tenant_id: str, content_hash: str) -> DocumentRecord | None:
        with self._lock:
            return next(
                (d for d in self._docs.values() if d.tenant_id == tenant_id and d.content_hash == content_hash),
                None,
            )

    def list_documents(self, tenant_id: str) -> list[DocumentRecord]:
        with self._lock:
            docs = [d for d in self._docs.values() if d.tenant_id == tenant_id]
        return sorted(docs, key=lambda d: d.created_at, reverse=True)

    def status_counts(self, tenant_id: str) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for d in self._docs.values():
                if d.tenant_id == tenant_id:
                    key = "archived" if d.archived_at is not None else d.status
                    out[key] = out.get(key, 0) + 1
            return out

    def query_documents(self, tenant_id: str, q: DocumentQuery) -> tuple[list[DocumentRecord], int]:
        with self._lock:
            docs = [d for d in self._docs.values() if d.tenant_id == tenant_id and _matches(d, q)]
        key, reverse = _SORT_KEYS.get(q.sort, _SORT_KEYS["newest"])
        docs.sort(key=key, reverse=reverse)
        start = (q.page - 1) * q.page_size
        return docs[start:start + q.page_size], len(docs)

    def update_document(self, tenant_id: str, document_id: str, **fields) -> DocumentRecord:
        with self._lock:
            doc = self.get_document(tenant_id, document_id)
            updated = replace(doc, **fields, updated_at=time.time())
            self._docs[document_id] = updated
            return updated

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        with self._lock:
            self.get_document(tenant_id, document_id)
            del self._docs[document_id]
            # Jobs go with their document (ON DELETE CASCADE in Postgres).
            self._jobs = {k: j for k, j in self._jobs.items() if j.document_id != document_id}

    # ── Jobs ──────────────────────────────────────────────────────────────
    def create_job(self, tenant_id: str, document_id: str, stage: str) -> JobRecord:
        with self._lock:
            self.get_document(tenant_id, document_id)
            now = time.time()
            job = JobRecord(uuid.uuid4().hex, tenant_id, document_id, stage, 0, None, now, now)
            self._jobs[job.id] = job
            return job

    def get_job(self, tenant_id: str, job_id: str) -> JobRecord:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.tenant_id != tenant_id:
                raise DocumentNotFound(job_id)
            return job

    def latest_job(self, tenant_id: str, document_id: str) -> JobRecord | None:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.tenant_id == tenant_id and j.document_id == document_id]
        return max(jobs, key=lambda j: (j.created_at, j.id)) if jobs else None

    def update_job(self, tenant_id: str, job_id: str, **fields) -> JobRecord:
        with self._lock:
            job = self.get_job(tenant_id, job_id)
            updated = replace(job, **fields, updated_at=time.time())
            self._jobs[job_id] = updated
            return updated

    def claim_job(self, tenant_id: str, job_id: str) -> JobRecord | None:
        """Atomically move a queued job to work in progress, counting the attempt.

        Returns None when the job is not queued (already claimed by another
        worker, finished, or deleted), so a job never runs twice at once.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.tenant_id != tenant_id or job.stage != "queued":
                return None
            claimed = replace(job, stage="extracting", attempts=job.attempts + 1,
                              error_message=None, updated_at=time.time())
            self._jobs[job_id] = claimed
            return claimed

    def jobs_in_stages(self, stages: frozenset[str] | set[str]) -> list[JobRecord]:
        """Across all tenants: used only by the background sweeper, never by a request."""
        with self._lock:
            return [j for j in self._jobs.values() if j.stage in stages]


_DOC_COLUMNS = (
    "d.id::text, d.tenant_id, COALESCE(d.uploaded_by, ''), d.title, COALESCE(d.file_name, d.title), "
    "d.source_type::text, COALESCE(d.content_type, ''), COALESCE(d.size_bytes, 0), "
    "COALESCE(d.content_hash, ''), COALESCE(d.storage_key, ''), d.status::text, d.error_message, "
    "(SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id), "
    "d.created_at, d.updated_at, d.processed_at, d.archived_at"
)
_JOB_COLUMNS = "id::text, tenant_id, document_id::text, stage, attempts, error_message, created_at, updated_at"


def _ts(value) -> float | None:
    return value.timestamp() if value is not None else None


class PostgresDocumentStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    @staticmethod
    def _doc(row) -> DocumentRecord:
        return DocumentRecord(
            id=row[0], tenant_id=row[1], uploaded_by=row[2], title=row[3], file_name=row[4],
            source_kind=row[5], content_type=row[6], size_bytes=int(row[7]), content_hash=row[8],
            storage_key=row[9], status=row[10], error_message=row[11], chunk_count=int(row[12]),
            created_at=_ts(row[13]) or 0.0, updated_at=_ts(row[14]) or 0.0, processed_at=_ts(row[15]),
            archived_at=_ts(row[16]),
        )

    @staticmethod
    def _job(row) -> JobRecord:
        return JobRecord(row[0], row[1], row[2], row[3], int(row[4]), row[5], _ts(row[6]) or 0.0, _ts(row[7]) or 0.0)

    def _select_doc(self, cur, where: str, params: tuple) -> DocumentRecord | None:
        cur.execute(f"SELECT {_DOC_COLUMNS} FROM documents d WHERE {where}", params)
        row = cur.fetchone()
        return self._doc(row) if row else None

    def create_document(self, record: DocumentRecord) -> DocumentRecord:
        from omni_modal.qa.pg_persistence import ensure_user_row  # noqa: PLC0415

        with self._pool.connection() as conn, conn.cursor() as cur:
            owner_uuid = ensure_user_row(cur, record.tenant_id, record.uploaded_by)
            # ON CONFLICT on the (tenant_id, content_hash) unique index makes the
            # duplicate check race-free: of two concurrent uploads, one inserts.
            cur.execute(
                """INSERT INTO documents
                     (id, tenant_id, owner_id, title, source_type, status, file_name, content_type,
                      size_bytes, content_hash, storage_key, uploaded_by)
                   VALUES (%s, %s, %s, %s, %s::document_source_type, 'uploaded', %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, content_hash) WHERE content_hash IS NOT NULL DO NOTHING
                   RETURNING id""",
                (record.id, record.tenant_id, owner_uuid, record.title, record.source_kind, record.file_name,
                 record.content_type, record.size_bytes, record.content_hash, record.storage_key,
                 record.uploaded_by),
            )
            if cur.fetchone() is None:
                existing = self._select_doc(
                    cur, "d.tenant_id = %s AND d.content_hash = %s", (record.tenant_id, record.content_hash)
                )
                if existing is None:  # the conflicting upload was deleted in between
                    raise RuntimeError("A matching upload was removed at the same moment. Try again.")
                raise DuplicateDocument(existing)
            return self._select_doc(cur, "d.id = %s", (record.id,))

    def get_document(self, tenant_id: str, document_id: str) -> DocumentRecord:
        with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                uuid.UUID(document_id)
            except ValueError as exc:
                raise DocumentNotFound(document_id) from exc
            doc = self._select_doc(cur, "d.id = %s AND d.tenant_id = %s", (document_id, tenant_id))
        if doc is None:
            raise DocumentNotFound(document_id)
        return doc

    def find_by_hash(self, tenant_id: str, content_hash: str) -> DocumentRecord | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            return self._select_doc(cur, "d.tenant_id = %s AND d.content_hash = %s", (tenant_id, content_hash))

    def list_documents(self, tenant_id: str) -> list[DocumentRecord]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_DOC_COLUMNS} FROM documents d WHERE d.tenant_id = %s ORDER BY d.created_at DESC LIMIT 500",
                (tenant_id,),
            )
            return [self._doc(r) for r in cur.fetchall()]

    _ORDER = {
        "newest": "d.created_at DESC, d.id",
        "oldest": "d.created_at ASC, d.id",
        "title": "lower(d.title) ASC, d.id",
        "size": "COALESCE(d.size_bytes, 0) DESC, d.id",
    }

    def status_counts(self, tenant_id: str) -> dict[str, int]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT CASE WHEN archived_at IS NOT NULL THEN 'archived' ELSE status::text END, count(*)
                   FROM documents WHERE tenant_id = %s GROUP BY 1""",
                (tenant_id,),
            )
            return {row[0]: int(row[1]) for row in cur.fetchall()}

    def query_documents(self, tenant_id: str, q: DocumentQuery) -> tuple[list[DocumentRecord], int]:
        where = ["d.tenant_id = %s", "(d.archived_at IS NOT NULL) = %s"]
        params: list = [tenant_id, q.archived]
        if q.status:
            where.append("d.status::text = %s")
            params.append(q.status)
        if q.source_kind:
            where.append("d.source_type::text = %s")
            params.append(q.source_kind)
        if q.uploaded_by:
            where.append("d.uploaded_by = %s")
            params.append(q.uploaded_by)
        if q.document_ids is not None:
            where.append("d.id::text = ANY(%s)")
            params.append(list(q.document_ids))
        if q.text.strip():
            # Escape LIKE wildcards so a search for "100%" means the text "100%".
            pattern = "%" + q.text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(lower(d.title) LIKE %s OR lower(COALESCE(d.file_name, '')) LIKE %s)")
            params += [pattern, pattern]
        clause = " AND ".join(where)
        order = self._ORDER.get(q.sort, self._ORDER["newest"])
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM documents d WHERE {clause}", tuple(params))
            total = int(cur.fetchone()[0])
            cur.execute(
                f"SELECT {_DOC_COLUMNS} FROM documents d WHERE {clause} ORDER BY {order} LIMIT %s OFFSET %s",
                (*params, q.page_size, (q.page - 1) * q.page_size),
            )
            rows = cur.fetchall()
        return [self._doc(r) for r in rows], total

    _UPDATABLE = {"status", "error_message", "processed_at", "title", "archived_at"}

    def update_document(self, tenant_id: str, document_id: str, **fields) -> DocumentRecord:
        unknown = set(fields) - self._UPDATABLE - {"chunk_count"}
        if unknown:
            raise ValueError(f"Cannot update document fields: {sorted(unknown)}")
        fields.pop("chunk_count", None)  # derived from document_chunks
        sets, params = ["updated_at = now()"], []
        for name, value in fields.items():
            if name == "status":
                sets.append("status = %s::document_status")
                params.append(value)
            elif name in ("processed_at", "archived_at"):
                sets.append(f"{name} = CASE WHEN %s THEN now() ELSE NULL END")
                params.append(value is not None)
            else:
                sets.append(f"{name} = %s")
                params.append(value)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE documents SET {', '.join(sets)} WHERE id = %s AND tenant_id = %s RETURNING id",
                (*params, document_id, tenant_id),
            )
            if cur.fetchone() is None:
                raise DocumentNotFound(document_id)
            return self._select_doc(cur, "d.id = %s", (document_id,))

    def delete_document(self, tenant_id: str, document_id: str) -> None:
        # Chunks, embeddings, entities and jobs go with it (ON DELETE CASCADE).
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s AND tenant_id = %s", (document_id, tenant_id))
            if cur.rowcount == 0:
                raise DocumentNotFound(document_id)

    def create_job(self, tenant_id: str, document_id: str, stage: str) -> JobRecord:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO ingestion_jobs (id, tenant_id, document_id, stage)
                    SELECT %s, %s, d.id, %s FROM documents d WHERE d.id = %s AND d.tenant_id = %s
                    RETURNING {_JOB_COLUMNS}""",
                (uuid.uuid4().hex, tenant_id, stage, document_id, tenant_id),
            )
            row = cur.fetchone()
        if row is None:
            raise DocumentNotFound(document_id)
        return self._job(row)

    def get_job(self, tenant_id: str, job_id: str) -> JobRecord:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_JOB_COLUMNS} FROM ingestion_jobs WHERE id = %s AND tenant_id = %s",
                        (job_id, tenant_id))
            row = cur.fetchone()
        if row is None:
            raise DocumentNotFound(job_id)
        return self._job(row)

    def latest_job(self, tenant_id: str, document_id: str) -> JobRecord | None:
        try:
            uuid.UUID(document_id)
        except ValueError:
            return None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT {_JOB_COLUMNS} FROM ingestion_jobs WHERE tenant_id = %s AND document_id = %s::uuid
                    ORDER BY created_at DESC, id DESC LIMIT 1""",
                (tenant_id, document_id),
            )
            row = cur.fetchone()
        return self._job(row) if row else None

    def update_job(self, tenant_id: str, job_id: str, **fields) -> JobRecord:
        unknown = set(fields) - {"stage", "attempts", "error_message"}
        if unknown:
            raise ValueError(f"Cannot update job fields: {sorted(unknown)}")
        sets = ", ".join(f"{name} = %s" for name in fields)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE ingestion_jobs SET {sets}, updated_at = now()
                    WHERE id = %s AND tenant_id = %s RETURNING {_JOB_COLUMNS}""",
                (*fields.values(), job_id, tenant_id),
            )
            row = cur.fetchone()
        if row is None:
            raise DocumentNotFound(job_id)
        return self._job(row)

    def claim_job(self, tenant_id: str, job_id: str) -> JobRecord | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE ingestion_jobs
                    SET stage = 'extracting', attempts = attempts + 1, error_message = NULL, updated_at = now()
                    WHERE id = %s AND tenant_id = %s AND stage = 'queued'
                    RETURNING {_JOB_COLUMNS}""",
                (job_id, tenant_id),
            )
            row = cur.fetchone()
        return self._job(row) if row else None

    def jobs_in_stages(self, stages) -> list[JobRecord]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_JOB_COLUMNS} FROM ingestion_jobs WHERE stage = ANY(%s)", (list(stages),))
            return [self._job(r) for r in cur.fetchall()]


_in_memory: InMemoryDocumentStore | None = None
_lock = threading.Lock()


def select_document_store():
    """Postgres-backed when configured (records survive restarts), else in-process."""
    import os  # noqa: PLC0415

    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return PostgresDocumentStore(get_connection_pool())
        except Exception as exc:  # noqa: BLE001 - fall back to the in-process store
            from omni_modal.observability import observability  # noqa: PLC0415

            observability.capture_message(
                f"DATABASE_URL is set but the document store could not use Postgres ({exc}); "
                "upload records will NOT survive a restart.",
                operation="documents.store.select", level="error",
            )
    global _in_memory
    with _lock:
        if _in_memory is None:
            _in_memory = InMemoryDocumentStore()
        return _in_memory
