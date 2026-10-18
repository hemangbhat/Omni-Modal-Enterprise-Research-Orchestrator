# M2 Durable Ingestion Implementation Plan

**Goal:** An upload survives everything short of the user deleting it. The file goes straight to object storage, the database records the document and every processing stage from the first click, the same file is never processed twice, and the user can delete, reprocess and download their documents. A backend restart resumes unfinished work.

**Spec:** `docs/superpowers/specs/2026-10-11-omero-completion-design.md`, section 7, "M2".

## Architecture

A new package `omni_modal/documents/`:

- `store.py`: document and ingestion-job records, with an in-memory store and a Postgres store (selected by `DATABASE_URL`, as for sessions).
- `service.py`: `DocumentService`, which owns the flow (upload slot, confirm, run job, delete, reprocess, download, resume) and calls the existing pipeline, storage adapter, vector store, entity service and query cache.
- `dispatch.py`: an in-process worker thread that runs job ids. Render's free tier has no separate worker, so jobs run in the API process; their record lives in the database, so a restart picks them up again.

Upload flow:

```
POST /documents/uploads  {filename, size_bytes, content_type, sha256, title?, workspace_id?}
   -> duplicate? return the existing document (200, duplicate: true)
   -> else create document (status uploaded) + job (stage awaiting_upload)
      and return where to send the bytes:
        S3 configured: a presigned form POST (10 minutes) whose policy accepts exactly the declared size
        otherwise:     PUT /documents/{id}/content  (authenticated, uploader only, size-capped while reading)
send the bytes            they land under a temporary "incoming" key
POST /documents/{id}/complete   checks the size, moves the bytes to the final key, queues the job
GET  /documents/{id}            document + latest job stage, for live progress
```

Job stages: `awaiting_upload → queued → extracting | transcribing → chunking → embedding → done | failed`. The document is marked ready only after its chunks and embeddings are committed. The stored file's SHA-256 is checked against the declared one before processing.

## Decisions made while planning

1. **Redis is not wired into the new flow.** The spec kept Redis as the job transport, but the deployment has no Redis and no separate worker. The job record lives in Postgres and an in-process worker runs it; the existing Redis queue module is left as a library and the README says so. Adding a Redis dispatcher later means changing only `dispatch.py`.
2. **Who may delete and reprocess:** the uploader, or an admin of the tenant. Anyone in the tenant with document access may download.
3. **Recovery runs continuously.** A sweeper thread (every minute, and at startup) requeues jobs with no progress for 10 minutes, re-sends queued jobs that were never picked up, and removes upload slots abandoned for 24 hours. Claiming a job is one atomic update, so a job never runs twice at once. Retries back off (5 s, then 10 s).
4. **`POST /ingest/local` is disabled unless `OMERO_ALLOW_LOCAL_INGEST=true`.** It let any researcher make the server read any file path on the server.
5. **`GET /ingest/jobs/{id}` checks the tenant.** It previously returned any job to any signed-in user.
6. **`POST /ingest/upload` (base64 JSON) stays** for scripts and API clients, rebuilt on the same service so it gets idempotency and job records too.
7. Upload quota is counted once per new document, not for duplicates.

## Schema

`packages/db/drizzle/0012_durable_ingestion.sql` (0011 is the RLS fix on its own branch):

- `documents` gains `file_name`, `content_type`, `size_bytes`, `content_hash`, `storage_key`, `error_message`, `uploaded_by` (the auth user id) and a unique index on `(tenant_id, content_hash)`.
- New `ingestion_jobs (id, tenant_id, document_id → documents ON DELETE CASCADE, stage, attempts, error_message, created_at, updated_at)`, with the same RLS policy shape as 0010.

## Frontend

- `lib/documents-api.ts`: hash the file in the browser, request a slot, upload with progress, confirm, poll the job.
- Upload page: a stage list per file (uploading with percent, queued, extracting or transcribing, chunking, embedding, ready) and the real error message on failure; duplicates say "Already in your library".
- Documents page: size, status, download, delete with confirmation, reprocess for failed documents.

## Review focus

1. User B cannot read, download, delete or reprocess user A's document across tenants; inside one tenant only the uploader or an admin can delete or reprocess.
2. A file larger than the limit is refused on the local upload route without being fully read into memory.
3. A job interrupted by a restart is resumed, and gives up after three attempts.
4. Deleting removes the stored object, chunks, embeddings, entities and cached answers.
5. The same file uploaded twice produces one document.

## Changes after the independent review

The review found no cross-tenant path. It found, and these were fixed with tests: a document deleted during processing could be re-created by the persistence upsert; two workers could run one job; a failed retry write left a job stuck until restart; re-adding a file whose upload broke reported it as done; the upload route buffered three copies of the body and read it before checking the uploader; a presigned PUT had no size limit and stayed usable after completion; rejected uploads used quota; Groq rate limits failed the file instead of retrying; raw database errors reached the user; a failed reprocess kept the old chunks searchable; the Documents page polled forever for abandoned slots.
