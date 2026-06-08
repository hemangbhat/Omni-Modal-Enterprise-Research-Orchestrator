import { neon } from "@neondatabase/serverless";
import { requireDatabaseUrl } from "./env.mjs";

const sql = neon(requireDatabaseUrl());

await sql`CREATE EXTENSION IF NOT EXISTS vector`;
await sql`CREATE EXTENSION IF NOT EXISTS pgcrypto`;

console.log("Database extensions enabled: vector, pgcrypto");
