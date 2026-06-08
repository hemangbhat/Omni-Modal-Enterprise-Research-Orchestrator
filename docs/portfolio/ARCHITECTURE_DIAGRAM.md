# Architecture Diagram — Omni-Modal Enterprise Research Orchestrator

## Request Pipeline (Phase 10+)

```
Browser
  │
  │  GET/POST  +  sentry-trace, baggage, X-Correlation-ID, Authorization: Bearer <JWT>
  ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Python HTTP Backend (main.py)                   │
│                                                                  │
│  1. SlidingWindowRateLimiter.check_tenant / check_user          │
│     → 429 + Retry-After if exceeded                             │
│                                                                  │
│  2. Auth Middleware (security/auth.py)                          │
│     → verify_jwt(HS256) → JwtClaims{tenant_id, user_id, roles} │
│     → 401 if invalid / missing                                  │
│                                                                  │
│  3. RBAC (security/rbac.py)                                     │
│     → assert_endpoint_roles(path, roles)                        │
│     → 403 if role insufficient                                  │
│                                                                  │
│  4. InputValidator (security/input_validation.py)               │
│     → body size ≤ 1 MiB, query ≤ 4096 chars, UUID v4 doc_id   │
│     → 400/413 on violation                                      │
│                                                                  │
│  5. observability.continue_trace(sentry-trace headers)          │
│                                                                  │
└────────┬──────────────────┬───────────────────────────────────┘
         │                  │
    POST /ingest/local   POST /query or /query/stream
         │                  │
         ▼                  ▼
┌─────────────────┐  ┌────────────────────────────────────────────┐
│ AsyncIngestion  │  │         DeterministicAgentGraph            │
│ Queue           │  │                                            │
│                 │  │  ValidateRequestNode                       │
│ enqueue()       │  │  → InternalRetrievalNode                  │
│ → 202 Accepted  │  │     └── PgVectorChunkRetriever            │
│                 │  │         ├── QueryCache.get(key)           │
│ BackgroundWorker│  │         │   (hit: return cached)          │
│ (daemon thread) │  │         └── pool.connection()             │
│ └── Pipeline    │  │             → pgvector cosine search      │
│     └── Batch  │  │         └── QueryCache.set(key, result)   │
│         Embedder│  │                                            │
│                 │  │  MissingDataDetectionNode                 │
│ cache.evict_    │  │  → ExternalDelegationNode                 │
│ tenant() on     │  │     └── Redactor.redact_request()        │
│ completion      │  │         └── HttpA2AResearchClient         │
│                 │  │             (+ retry, + rate limit)       │
└─────────────────┘  │  ExternalEvidenceMergeNode               │
                     │  → ReasoningSynthesisNode                 │
                     │  → ControlledFallbackNode                 │
                     └────────────────────────────────────────────┘
                                    │
                                    │ JSON / SSE stream
                                    ▼
                               Browser
```

## Data Flow — Ingestion

```
POST /ingest/local
  │
  ├── UploadSafetyGuard (size ≤ 50 MiB, MIME sniff)
  │
  ├── MultimodalIngestionPipeline.ingest()
  │   ├── LocalPdfTextExtractor  (pypdf)
  │   │   or LocalWhisperTranscriber (Whisper CLI)
  │   ├── normalize_text()
  │   ├── DeterministicChunker (240 words, 40 overlap)
  │   └── BatchEmbedder.write_chunks()
  │       └── pool.connection()
  │           └── INSERT document_chunks ... ON CONFLICT DO UPDATE
  │           └── INSERT embeddings ... ON CONFLICT DO UPDATE
  │
  └── AuditSink.record_event("ingestion:complete")
```

## Data Flow — Query Retrieval

```
POST /query
  │
  ├── QueryCache.get(SHA-256(question, tenant_id, top_k, min_similarity))
  │   └── cache HIT → return immediately (< 50ms)
  │
  └── cache MISS →
      ├── HashingQueryEmbeddingProvider.embed_query(question)
      │   (production: replace with OpenAI / Cohere)
      │
      ├── pool.connection()
      │   └── SELECT ... FROM embeddings
      │         JOIN document_chunks, documents
      │         WHERE tenant_id = %s
      │         ORDER BY embedding <=> %s::vector   ← HNSW index
      │         LIMIT top_k
      │
      └── QueryCache.set(key, results)
          └── return results
```

## Security Layers

```
Request
  │
  │  Layer 0: Rate Limiting (before body parse)
  │  ├── 60 req/min per tenant
  │  ├── 20 req/min per user
  │  └── 10 delegations/hr per tenant
  │
  │  Layer 1: Authentication
  │  └── JWT HS256 — tenant_id + user_id + roles + exp
  │
  │  Layer 2: Authorization (RBAC)
  │  └── /query → researcher|admin
  │  └── /ingest/local → researcher|admin
  │
  │  Layer 3: Input Validation
  │  └── body ≤ 1 MiB, query ≤ 4096, document_id is UUID v4
  │
  │  Layer 4: Upload Safety (ingestion only)
  │  └── MIME sniff, extension check, 50 MiB limit
  │
  │  Layer 5: Document-Level Access Control
  │  └── private (owner only) / tenant (any user) / restricted (allowlist)
  │
  │  Layer 6: Credential Boundary
  │  └── SecretRef.__str__() always returns "<redacted>"
  │  └── PII scrubbing in Sentry before_send hook
  │
  │  Layer 7: Content Redaction (delegation only)
  │  └── Redactor strips chunk text from A2A payloads
  │  └── ContentLeakError aborts delegation
```

## Database Schema (simplified)

```sql
documents         (id, tenant_id, title, source_type, status, owner_id, metadata)
document_chunks   (id, tenant_id, document_id, chunk_index, content, content_hash, metadata)
embeddings        (id, tenant_id, document_id, chunk_id, embedding vector(N), embedding_model, dimensions)
audit_logs        (id, tenant_id, actor_user_id, action, resource_type, resource_id, status, metadata, created_at)

-- HNSW index (0002_perf.sql)
CREATE INDEX embeddings_vector_hnsw_idx ON embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Covering index
CREATE INDEX embeddings_tenant_doc_covering_idx
  ON embeddings (tenant_id, document_id) INCLUDE (chunk_id);
```

## Technology Decisions Summary

| Concern | Choice | Reason |
|---------|--------|--------|
| Frontend framework | Next.js 16 App Router | Industry standard, RSC, edge middleware |
| Backend framework | Python stdlib `http.server` | Demonstrates low-level HTTP knowledge |
| Vector DB | PostgreSQL + pgvector | No separate vector DB dependency; Drizzle already in use |
| Embedding | Hash-based stub | Framework-agnostic; swap in OpenAI/Cohere with one class |
| Auth | HS256 JWT (stdlib `hmac`) | No external auth library needed; constant-time comparison |
| Observability | Sentry | Distributed tracing across both runtimes |
| Testing | `unittest` + Hypothesis | Property-based tests catch invariant violations |
| Caching | `cachetools.TTLCache` | In-process, zero dependencies for single-node |
| Job queue | `queue.Queue` + thread | In-process, matches the single-process deployment model |
| Connection pool | `psycopg_pool` | Official companion to psycopg3 |
