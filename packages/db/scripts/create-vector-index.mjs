import { neon } from "@neondatabase/serverless";
import { requireDatabaseUrl } from "./env.mjs";

const sql = neon(requireDatabaseUrl());

await sql`
  CREATE INDEX IF NOT EXISTS embeddings_vector_hnsw_idx
  ON embeddings USING hnsw (embedding vector_cosine_ops)
`;

console.log("Vector index ready: embeddings_vector_hnsw_idx");
