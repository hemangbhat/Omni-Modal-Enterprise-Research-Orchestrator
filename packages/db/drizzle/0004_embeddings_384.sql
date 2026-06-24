-- Phase C: align the pgvector column with the active 384-dim local embedding
-- model (BAAI/bge-small-en-v1.5) so vectors are actually inserted and queried
-- end-to-end. The previous vector(1536) column targeted OpenAI-dim models.
--
-- NOTE: this drops existing embedding rows because a 1536-dim vector cannot be
-- cast to 384 dims. In dev that table is empty / re-ingestable. If you keep
-- OpenAI (1536) embeddings, do NOT apply this migration.

DROP INDEX IF EXISTS embeddings_vector_hnsw_idx;

TRUNCATE TABLE embeddings;

ALTER TABLE embeddings
  ALTER COLUMN embedding TYPE vector(384),
  ALTER COLUMN dimensions SET DEFAULT 384;

CREATE INDEX IF NOT EXISTS embeddings_vector_hnsw_idx
  ON embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
