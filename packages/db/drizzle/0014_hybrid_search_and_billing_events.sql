-- M4: keyword index for hybrid retrieval, and a log of applied Stripe events.

-- Full-text index matching the expression the hybrid retriever queries.
CREATE INDEX IF NOT EXISTS document_chunks_content_fts_idx
  ON document_chunks USING gin (to_tsvector('english', content));

-- Every Stripe event applied once: a duplicate delivery is ignored, and a plan
-- event older than the last one applied to the organization is ignored.
CREATE TABLE IF NOT EXISTS stripe_events (
  event_id text PRIMARY KEY,
  event_type text NOT NULL,
  tenant_id text,
  stripe_created_at timestamptz,
  processed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stripe_events_tenant_idx ON stripe_events (tenant_id, stripe_created_at DESC);
