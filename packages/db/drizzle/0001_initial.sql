CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
  CREATE TYPE document_status AS ENUM (
    'uploaded',
    'processing',
    'ready',
    'failed',
    'archived'
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE document_source_type AS ENUM (
    'pdf',
    'audio',
    'transcript',
    'note',
    'web'
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE entity_type AS ENUM (
    'person',
    'organization',
    'location',
    'product',
    'metric',
    'topic',
    'date',
    'other'
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE audit_action AS ENUM (
    'create',
    'read',
    'update',
    'delete',
    'upload',
    'process',
    'search',
    'export'
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id varchar(128) NOT NULL,
  email varchar(320) NOT NULL,
  display_name text NOT NULL,
  role varchar(80) NOT NULL DEFAULT 'researcher',
  is_active boolean NOT NULL DEFAULT true,
  access_metadata jsonb NOT NULL DEFAULT '{"visibility":"private"}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_email_unique
  ON users (tenant_id, email);

CREATE INDEX IF NOT EXISTS users_tenant_idx
  ON users (tenant_id);

CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id varchar(128) NOT NULL,
  owner_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  title text NOT NULL,
  source_type document_source_type NOT NULL,
  source_uri text,
  status document_status NOT NULL DEFAULT 'uploaded',
  language varchar(16) NOT NULL DEFAULT 'en',
  access_metadata jsonb NOT NULL DEFAULT '{"visibility":"private"}'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz
);

CREATE INDEX IF NOT EXISTS documents_tenant_idx
  ON documents (tenant_id);

CREATE INDEX IF NOT EXISTS documents_owner_idx
  ON documents (owner_id);

CREATE INDEX IF NOT EXISTS documents_status_idx
  ON documents (status);

CREATE INDEX IF NOT EXISTS documents_source_type_idx
  ON documents (source_type);

CREATE TABLE IF NOT EXISTS document_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id varchar(128) NOT NULL,
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index integer NOT NULL,
  content text NOT NULL,
  content_hash varchar(128) NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS document_chunks_document_index_unique
  ON document_chunks (document_id, chunk_index);

CREATE INDEX IF NOT EXISTS document_chunks_tenant_idx
  ON document_chunks (tenant_id);

CREATE INDEX IF NOT EXISTS document_chunks_document_idx
  ON document_chunks (document_id);

CREATE INDEX IF NOT EXISTS document_chunks_content_hash_idx
  ON document_chunks (content_hash);

CREATE TABLE IF NOT EXISTS embeddings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id varchar(128) NOT NULL,
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id uuid NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
  embedding vector(1536) NOT NULL,
  embedding_model text NOT NULL,
  dimensions integer NOT NULL DEFAULT 1536,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS embeddings_tenant_idx
  ON embeddings (tenant_id);

CREATE INDEX IF NOT EXISTS embeddings_document_idx
  ON embeddings (document_id);

CREATE INDEX IF NOT EXISTS embeddings_chunk_idx
  ON embeddings (chunk_id);

CREATE INDEX IF NOT EXISTS embeddings_vector_hnsw_idx
  ON embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS extracted_entities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id varchar(128) NOT NULL,
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id uuid REFERENCES document_chunks(id) ON DELETE SET NULL,
  type entity_type NOT NULL,
  value text NOT NULL,
  normalized_value text,
  confidence integer,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS extracted_entities_tenant_idx
  ON extracted_entities (tenant_id);

CREATE INDEX IF NOT EXISTS extracted_entities_document_idx
  ON extracted_entities (document_id);

CREATE INDEX IF NOT EXISTS extracted_entities_chunk_idx
  ON extracted_entities (chunk_id);

CREATE INDEX IF NOT EXISTS extracted_entities_type_value_idx
  ON extracted_entities (type, value);

CREATE TABLE IF NOT EXISTS audit_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id varchar(128) NOT NULL,
  actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  action audit_action NOT NULL,
  resource_type varchar(80) NOT NULL,
  resource_id uuid,
  ip_address varchar(64),
  user_agent text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_logs_tenant_idx
  ON audit_logs (tenant_id);

CREATE INDEX IF NOT EXISTS audit_logs_actor_idx
  ON audit_logs (actor_user_id);

CREATE INDEX IF NOT EXISTS audit_logs_resource_idx
  ON audit_logs (resource_type, resource_id);

CREATE INDEX IF NOT EXISTS audit_logs_created_at_idx
  ON audit_logs (created_at);
