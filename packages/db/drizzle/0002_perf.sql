-- 0002_perf.sql — HNSW tuning + covering index
-- Phase 11: Performance and Scalability
--
-- Index parameters: m=16, ef_construction=64, ef_search=40
-- m=16 is pgvector's recommended default; ef_construction=64 doubles the
-- default (32) to trade build time for higher recall.
--
-- NOTE: Recall and latency for this configuration have NOT been measured
-- against a real corpus yet. Run `python -m omni_modal.benchmark` against a
-- populated database to produce real numbers, then record them here.
-- Do not cite recall/latency figures until they are measured.

-- Drop and recreate HNSW index with explicit tuning parameters
DROP INDEX IF EXISTS embeddings_vector_hnsw_idx;

CREATE INDEX embeddings_vector_hnsw_idx
  ON embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Session-level ef_search default (higher = better recall, slower search).
ALTER DATABASE current_database() SET hnsw.ef_search = 40;

-- Covering index to eliminate heap fetch for tenant filter
-- INCLUDE(chunk_id) allows the planner to satisfy tenant+document filter
-- from the index alone without a heap lookup.
CREATE INDEX IF NOT EXISTS embeddings_tenant_doc_covering_idx
  ON embeddings (tenant_id, document_id)
  INCLUDE (chunk_id);
