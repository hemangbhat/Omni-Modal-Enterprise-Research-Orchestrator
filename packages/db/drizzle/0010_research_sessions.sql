-- 0010_research_sessions.sql — saved research and assistant conversations.
--
-- research_sessions: one conversation, private to the user who started it.
-- `kind` separates the Research page ('research') from the site-wide
-- assistant ('assistant').
--
-- research_messages: the turns of a session, in order. Assistant turns keep
-- the citations they were answered with (title, page or timestamp, snippet),
-- so an old answer can be checked against its evidence later.

CREATE TABLE IF NOT EXISTS research_sessions (
  id text PRIMARY KEY,
  tenant_id text NOT NULL,
  user_id text NOT NULL,
  kind text NOT NULL DEFAULT 'research' CHECK (kind IN ('research', 'assistant')),
  title text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS research_sessions_owner_idx
  ON research_sessions (tenant_id, user_id, kind, updated_at DESC);

CREATE TABLE IF NOT EXISTS research_messages (
  id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES research_sessions (id) ON DELETE CASCADE,
  tenant_id text NOT NULL,
  seq integer NOT NULL,
  role text NOT NULL CHECK (role IN ('user', 'assistant')),
  content text NOT NULL,
  status text,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, seq)
);

-- Tenant isolation as in 0008_rls.sql, permissive while the app.tenant_id GUC
-- is unset. NULLIF also treats an empty GUC as unset: after a transaction that
-- used SET LOCAL commits, a pooled connection reports '' rather than NULL.
ALTER TABLE research_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON research_sessions;
CREATE POLICY tenant_isolation ON research_sessions
  USING (NULLIF(current_setting('app.tenant_id', true), '') IS NULL
         OR tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (NULLIF(current_setting('app.tenant_id', true), '') IS NULL
         OR tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE research_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON research_messages;
CREATE POLICY tenant_isolation ON research_messages
  USING (NULLIF(current_setting('app.tenant_id', true), '') IS NULL
         OR tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (NULLIF(current_setting('app.tenant_id', true), '') IS NULL
         OR tenant_id = current_setting('app.tenant_id', true));
