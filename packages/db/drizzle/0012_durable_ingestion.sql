-- 0012_durable_ingestion.sql — a document row from the first click, and a job
-- record for every processing run.
--
-- documents gains what the upload flow knows before processing starts: the
-- original file name, type and size, the SHA-256 of its bytes, where the bytes
-- are stored, who uploaded it (the auth user id), and the error that stopped
-- processing. The unique (tenant_id, content_hash) index makes "upload the
-- same file twice" return the existing document, race-free.
--
-- ingestion_jobs records each processing run: its stage (awaiting_upload,
-- queued, extracting, transcribing, chunking, embedding, done, failed), how
-- many attempts it took, and the error. Unfinished jobs are resumed when the
-- API starts.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_name text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_type text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS size_bytes bigint;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash varchar(64);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS storage_key text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_by text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_message text;

CREATE UNIQUE INDEX IF NOT EXISTS documents_tenant_content_hash_unique
  ON documents (tenant_id, content_hash) WHERE content_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingestion_jobs (
  id text PRIMARY KEY,
  tenant_id varchar(128) NOT NULL,
  document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
  stage text NOT NULL CHECK (stage IN (
    'awaiting_upload', 'queued', 'extracting', 'transcribing',
    'chunking', 'embedding', 'done', 'failed'
  )),
  attempts integer NOT NULL DEFAULT 0,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingestion_jobs_document_idx
  ON ingestion_jobs (tenant_id, document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_jobs_unfinished_idx
  ON ingestion_jobs (stage) WHERE stage NOT IN ('done', 'failed');

-- Tenant isolation in the same shape as 0010 (an empty GUC counts as unset).
ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_jobs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON ingestion_jobs;
CREATE POLICY tenant_isolation ON ingestion_jobs
  USING (NULLIF(current_setting('app.tenant_id', true), '') IS NULL
         OR tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (NULLIF(current_setting('app.tenant_id', true), '') IS NULL
         OR tenant_id = current_setting('app.tenant_id', true));
