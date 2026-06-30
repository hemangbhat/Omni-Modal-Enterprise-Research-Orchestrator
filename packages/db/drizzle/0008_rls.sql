-- 0008_rls.sql — Row-Level Security (defense-in-depth tenant isolation)
--
-- The application already filters every query by tenant_id. RLS adds a second,
-- database-enforced layer so a single forgotten WHERE clause cannot leak data
-- across tenants. Isolation is keyed on a per-transaction GUC, `app.tenant_id`,
-- which the app sets via `omni_modal.db.rls.set_tenant(conn, tenant_id)`
-- (issued as `SELECT set_config('app.tenant_id', $1, true)` inside the
-- transaction that runs tenant-scoped queries).
--
-- Rollout model (intentionally non-breaking):
--   * Policies are PERMISSIVE when the GUC is unset (current_setting(...) IS NULL),
--     so migrations, admin tooling, and the offline/in-memory path keep working.
--   * When the app sets app.tenant_id, the database enforces tenant_id = GUC for
--     SELECT/INSERT/UPDATE/DELETE — even for the table owner (FORCE).
--   * To move to STRICT isolation, drop the "IS NULL" escape from each policy
--     once you've confirmed every tenant-scoped code path sets the GUC.
--
-- Apply with: python scripts/migrate.py   (or psql -f this file)

DO $$
DECLARE
  t text;
  tenant_tables text[] := ARRAY[
    'users', 'documents', 'document_chunks', 'embeddings',
    'extracted_entities', 'audit_logs', 'audit_events',
    'organizations', 'org_workspaces', 'usage_counters', 'notifications'
  ];
BEGIN
  FOREACH t IN ARRAY tenant_tables LOOP
    -- Only act on tables that actually exist in this database.
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = t) THEN
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
      EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON %I '
        'USING (current_setting(''app.tenant_id'', true) IS NULL '
        '       OR tenant_id = current_setting(''app.tenant_id'', true)) '
        'WITH CHECK (current_setting(''app.tenant_id'', true) IS NULL '
        '       OR tenant_id = current_setting(''app.tenant_id'', true))',
        t
      );
    END IF;
  END LOOP;
END $$;
