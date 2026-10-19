-- M5: a workspace chooses whether questions its documents cannot answer may be
-- searched on the web. Off by default; only the redacted question is sent.
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS external_research_enabled boolean NOT NULL DEFAULT false;
