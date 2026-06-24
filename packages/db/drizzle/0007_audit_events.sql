-- Persistent audit sink for security and observability events (Phase B cont.).
-- Uses free-form action text (not the narrow audit_action enum) so every
-- event type the backend emits can be stored without schema changes.
-- The existing audit_logs table is kept for the compliance-report path.

CREATE TABLE IF NOT EXISTS audit_events (
  id          bigserial PRIMARY KEY,          -- monotonically increasing
  tenant_id   varchar(128) NOT NULL,
  user_id     varchar(128),                   -- nullable for system events
  action      text NOT NULL,                  -- e.g. "tool:search_documents"
  resource_type text NOT NULL,
  resource_id text,
  status      text NOT NULL DEFAULT 'ok',
  metadata    jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_events_tenant_idx
  ON audit_events (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_events_user_idx
  ON audit_events (user_id) WHERE user_id IS NOT NULL;
