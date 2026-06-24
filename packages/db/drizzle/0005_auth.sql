-- Phase E: real credential auth. Add a nullable password hash to users so the
-- email+password login flow can store PBKDF2 hashes. Nullable keeps existing
-- (SSO/dev-token) users valid. A partial unique index enforces one account per
-- email globally for login lookup by email.

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_unique
  ON users (lower(email))
  WHERE password_hash IS NOT NULL;
