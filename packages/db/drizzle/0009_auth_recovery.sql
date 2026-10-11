-- 0009_auth_recovery.sql — durable MFA enrollment and password-reset tokens.
--
-- user_mfa: one TOTP secret per (tenant, user). `last_counter` is the last
-- accepted 30-second time step, so a code cannot be replayed inside its window.
--
-- password_reset_tokens: single-use, expiring reset links. Only the SHA-256 of
-- the token is stored; the raw token exists only in the email sent to the user.

CREATE TABLE IF NOT EXISTS user_mfa (
  tenant_id text NOT NULL,
  user_id text NOT NULL,
  secret text NOT NULL,
  confirmed boolean NOT NULL DEFAULT false,
  last_counter bigint NOT NULL DEFAULT -1,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
  token_hash text PRIMARY KEY,
  email text NOT NULL,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS password_reset_tokens_email_idx
  ON password_reset_tokens (lower(email), created_at);

-- Same tenant-isolation policy shape as 0008_rls.sql (permissive while the
-- app.tenant_id GUC is unset). password_reset_tokens is deliberately excluded:
-- it is read before any tenant is known.
ALTER TABLE user_mfa ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_mfa FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON user_mfa;
CREATE POLICY tenant_isolation ON user_mfa
  USING (current_setting('app.tenant_id', true) IS NULL
         OR tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (current_setting('app.tenant_id', true) IS NULL
         OR tenant_id = current_setting('app.tenant_id', true));
