-- 0013_projects.sql — projects, their findings, notes, reports and activity;
-- stored entity labels; archived documents.
--
-- projects are shared by everyone in a tenant. Documents are linked to a
-- project through project_documents; a research session (private to its
-- owner) can belong to one project through research_sessions.project_id.
-- project_items holds findings (saved from research answers, keeping their
-- citations) and notes. project_reports holds compiled Markdown reports.
-- project_activity is the per-project activity log.
--
-- extracted_entities.label: the extractor's own labels (decision, risk,
-- action_item, owner, deadline, goal, kpi, company_acronym). The original
-- `type` enum does not have them, so rows written by the app use type 'other'.
--
-- documents.archived_at: an archived document is left out of research and of
-- the default document list until restored.

CREATE TABLE IF NOT EXISTS projects (
  id text PRIMARY KEY,
  tenant_id varchar(128) NOT NULL,
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  archived_at timestamptz
);
CREATE INDEX IF NOT EXISTS projects_tenant_idx ON projects (tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS project_documents (
  project_id text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
  document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
  tenant_id varchar(128) NOT NULL,
  added_by text NOT NULL,
  added_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, document_id)
);
CREATE INDEX IF NOT EXISTS project_documents_document_idx ON project_documents (document_id);

CREATE TABLE IF NOT EXISTS project_items (
  id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
  tenant_id varchar(128) NOT NULL,
  kind text NOT NULL CHECK (kind IN ('finding', 'note')),
  title text NOT NULL,
  body text NOT NULL DEFAULT '',
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_session_id text,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS project_items_project_idx ON project_items (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS project_reports (
  id text PRIMARY KEY,
  project_id text REFERENCES projects (id) ON DELETE CASCADE,
  tenant_id varchar(128) NOT NULL,
  source text NOT NULL CHECK (source IN ('project', 'session')),
  source_session_id text,
  title text NOT NULL,
  markdown text NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS project_reports_project_idx ON project_reports (tenant_id, project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS project_activity (
  id bigserial PRIMARY KEY,
  project_id text NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
  tenant_id varchar(128) NOT NULL,
  actor text NOT NULL,
  action text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS project_activity_project_idx ON project_activity (project_id, id DESC);
CREATE INDEX IF NOT EXISTS project_activity_tenant_idx ON project_activity (tenant_id, id DESC);

ALTER TABLE research_sessions ADD COLUMN IF NOT EXISTS project_id text
  REFERENCES projects (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS research_sessions_project_idx ON research_sessions (project_id);

ALTER TABLE extracted_entities ADD COLUMN IF NOT EXISTS label text;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS archived_at timestamptz;

-- Tenant isolation in the 0010 shape (an empty GUC counts as unset).
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['projects', 'project_documents', 'project_items', 'project_reports', 'project_activity'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I '
      'USING (NULLIF(current_setting(''app.tenant_id'', true), '''') IS NULL '
      '       OR tenant_id = current_setting(''app.tenant_id'', true)) '
      'WITH CHECK (NULLIF(current_setting(''app.tenant_id'', true), '''') IS NULL '
      '       OR tenant_id = current_setting(''app.tenant_id'', true))',
      t
    );
  END LOOP;
END $$;
