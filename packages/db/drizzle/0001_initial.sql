CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS research_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  source_type text NOT NULL,
  source_uri text,
  title text NOT NULL,
  body_text text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS research_documents_tenant_idx
  ON research_documents (tenant_id);

CREATE INDEX IF NOT EXISTS research_documents_source_idx
  ON research_documents (source_type);

CREATE TABLE IF NOT EXISTS extracted_entities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES research_documents(id),
  tenant_id text NOT NULL,
  label text NOT NULL,
  value text NOT NULL,
  confidence text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS extracted_entities_document_idx
  ON extracted_entities (document_id);

CREATE INDEX IF NOT EXISTS extracted_entities_tenant_label_idx
  ON extracted_entities (tenant_id, label);

CREATE TABLE IF NOT EXISTS document_embeddings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES research_documents(id),
  tenant_id text NOT NULL,
  embedding vector(1536) NOT NULL,
  model_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS document_embeddings_document_idx
  ON document_embeddings (document_id);

CREATE INDEX IF NOT EXISTS document_embeddings_tenant_idx
  ON document_embeddings (tenant_id);
