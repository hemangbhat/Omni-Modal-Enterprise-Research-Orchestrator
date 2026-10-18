-- 0011_rls_empty_guc.sql — treat an empty app.tenant_id GUC as "unset"
--
-- 0008_rls.sql and 0009_auth_recovery.sql made each tenant_isolation policy
-- permissive when `current_setting('app.tenant_id', true) IS NULL`. That only
-- holds on a connection that has never seen the GUC. Once a transaction runs
-- `set_config('app.tenant_id', x, true)` (SET LOCAL, see
-- omni_modal/db/rls.py) and commits, Postgres resets the placeholder to ''
-- rather than NULL. With RLS_ENFORCEMENT=true, every later query on that
-- pooled connection that does not set the GUC would then compare
-- tenant_id = '' — reading zero rows and failing every WITH CHECK.
--
-- This migration recreates those policies in the shape 0010 already uses:
-- NULLIF(..., '') IS NULL, so '' and NULL are both "no tenant bound".
-- 0008 and 0009 are left untouched because they are already applied.
--
-- Apply with: python scripts/migrate.py   (or psql -f this file)

DO $$
DECLARE
  t text;
  tenant_tables text[] := ARRAY[
    -- from 0008_rls.sql
    'users', 'documents', 'document_chunks', 'embeddings',
    'extracted_entities', 'audit_logs', 'audit_events',
    'organizations', 'org_workspaces', 'usage_counters', 'notifications',
    -- from 0009_auth_recovery.sql
    'user_mfa'
  ];
BEGIN
  FOREACH t IN ARRAY tenant_tables LOOP
    -- Only act on tables that actually exist in this database.
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = t) THEN
      EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON %I '
        'USING (NULLIF(current_setting(''app.tenant_id'', true), '''') IS NULL '
        '       OR tenant_id = current_setting(''app.tenant_id'', true)) '
        'WITH CHECK (NULLIF(current_setting(''app.tenant_id'', true), '''') IS NULL '
        '       OR tenant_id = current_setting(''app.tenant_id'', true))',
        t
      );
    END IF;
  END LOOP;
END $$;
