# Interview & Portfolio Preparation

## 1. Project Summary (2-minute version)

"I built a multi-tenant enterprise research platform that ingests PDF and audio documents, stores them as vector embeddings in PostgreSQL with pgvector, and answers natural language research queries. The system orchestrates an internal retrieval pipeline and optionally delegates to an external AI agent when internal data is insufficient.

I structured it as a 12-phase project to demonstrate how a real system evolves: starting from a clean foundation, adding the core pipeline, then layering in observability with Sentry distributed tracing, enterprise security controls like JWT auth and document-level access control, and finally performance optimizations including async ingestion, connection pooling, and query result caching.

The test suite has 275+ tests including Hypothesis property-based tests that verify correctness invariants — things like 'the retry decorator breadcrumb count always matches the attempt count' or 'the rate limiter never grants more than L requests in any rolling window of W seconds.' "

---

## 2. Resume Bullets

**Senior / Mid-level framing:**

- Designed and implemented a multi-tenant enterprise research orchestrator with a Python HTTP backend, Next.js 16 frontend, and PostgreSQL + pgvector for semantic document retrieval; built across 12 phases with 275+ tests
- Implemented full-stack Sentry distributed tracing with `sentry-trace`/`baggage` header propagation, PII scrubbing before payload transmission, and retry-with-fallback logic covering ingestion, retrieval, MCP tool calls, and A2A delegation
- Hardened the system for enterprise use: HS256 JWT authentication (constant-time verification), role-based access control, document-level visibility enforcement (`private`/`tenant`/`restricted`), sliding-window rate limiting, and content redaction before external AI delegation
- Optimized for throughput: replaced synchronous ingestion with an async BackgroundWorker queue, introduced a psycopg_pool connection pool singleton, and added an in-process LRU+TTL query cache with tenant eviction; built a batch embedding writer with `ON CONFLICT DO UPDATE` upsert (unit-tested; wired on the in-memory path)
- Validated correctness with 34+ Hypothesis property-based tests covering JWT round-trip fidelity, exponential backoff bounds, rate limiter window guarantees, cache eviction completeness, and batch partitioning invariants

**If space is tight (one bullet):**

- Built a multi-tenant AI research orchestrator (Python, Next.js, pgvector) with Sentry observability, JWT/RBAC security, and async ingestion; wired a Postgres-free in-memory path for end-to-end demos; validated with 295+ tests including 34+ Hypothesis property-based tests

---

## 3. Architecture Diagram (text version for whiteboard)

```
                    ┌──────────────────────────────┐
                    │   Next.js 16 Frontend         │
                    │   (React 19 + Tailwind CSS)   │
                    │   • Sentry error boundary      │
                    │   • apiRequest (X-Corr-ID,    │
                    │     sentry-trace headers)      │
                    │   • Client-side upload check  │
                    │   • middleware.ts (JWT guard)  │
                    └──────────┬───────────────────┘
                               │ HTTP + sentry-trace/baggage
                    ┌──────────▼───────────────────┐
                    │   Python HTTP Backend         │
                    │                               │
                    │  RateLimiter → JWT Auth        │
                    │  → RBAC → InputValidator       │
                    │                               │
                    │  AsyncIngestionQueue          │
                    │  └── BackgroundWorker         │
                    │      └── MultimodalPipeline   │
                    │          └── BatchEmbedder    │
                    │                               │
                    │  DeterministicAgentGraph      │
                    │  └── InternalRetrievalNode    │
                    │      └── PgVectorChunkRetriever│
                    │          (pool + cache)        │
                    │  └── ExternalDelegationNode   │
                    │      └── Redactor → A2AClient │
                    └──────────┬───────────────────┘
                               │ psycopg3
                    ┌──────────▼───────────────────┐
                    │   PostgreSQL + pgvector        │
                    │   documents, document_chunks  │
                    │   embeddings (HNSW index)     │
                    │   audit_logs                  │
                    └──────────────────────────────┘
```

---

## 4. Demo Script (5 minutes)

**Setup talking point:** "There's a working local demo that needs no database — ingestion, persistence, and retrieval run through an in-memory vector store. The embedder is a deterministic bag-of-words hash, so ranking is by token overlap rather than true semantics; swapping in a real embedding API is a one-class change. External AI delegation and Whisper transcription need real credentials/models, so those stay disabled by default."

**Step 0 — Working local demo (1 min)**
```
python scripts/seed_demo.py
```
Show that a sample query returns the most relevant seeded chunk ranked first — real cosine similarity over real persisted vectors, no mocks. Then mention the full browser flow: `/upload` posts a file to `/ingest/upload`, the background worker processes it, and `/research` streams the answer from `/query/stream`.

**Step 1 — Project structure (1 min)**
Open the repository root. Point to:
- `services/api/src/omni_modal/` — the Python backend modules
- `apps/web/src/` — the Next.js frontend
- `packages/db/drizzle/` — the SQL schema

**Step 2 — Security layer (1 min)**
Open `services/api/src/omni_modal/security/`. Show:
- `auth.py` — `verify_jwt` with constant-time HMAC comparison
- `rbac.py` — `ENDPOINT_ROLES` map
- `document_access.py` — `check_access` with three visibility modes
- `redactor.py` — fingerprint-based chunk detection in delegation payloads

**Step 3 — Test suite (1 min)**
Open `services/api/tests/`. Point to test files. Run or show output:
```
python -m unittest discover -s services/api/tests
# 295 tests, OK
```
Show one property test — e.g., `test_rate_limiter.py` — and explain what Hypothesis does.

**Step 4 — Observability (1 min)**
Open `services/api/src/omni_modal/observability.py`. Show:
- `continue_trace` — propagates `sentry-trace` header from frontend
- `scrub_pii` / `scrub_value` — strips emails, URLs, secrets before Sentry
- `before_send` hook integration

**Step 5 — Performance (1 min)**
Open `services/api/src/omni_modal/ingestion/async_queue.py` and `qa/cache.py`. Explain:
- Jobs enqueued in < 1ms; processing happens on a daemon thread
- `QueryCache` uses SHA-256 of (question, tenant_id, top_k, min_similarity) as key
- `evict_tenant()` invalidates all cached results when a new document is ingested

---

## 5. Common Interview Questions

**Q: Why Python stdlib `http.server` instead of FastAPI?**
A: Deliberate choice to show low-level HTTP handling — reading Content-Length, writing SSE streams manually, handling connection lifecycle. FastAPI would abstract all of that. For production, FastAPI or Starlette would be the right choice.

**Q: How does tenant isolation work?**
A: Three layers. At the data layer, every query uses `WHERE tenant_id = %s` bound parameters — no cross-tenant rows can be returned. At the access control layer, `DocumentAccessGuard` checks the `AccessMetadata.visibility` field before returning any document. At the rate limiter, buckets are keyed by `t:{tenant_id}` so tenants can't exhaust each other's quotas.

**Q: What's property-based testing and when is it better than unit tests?**
A: Unit tests cover cases you thought of. Property tests generate hundreds of random inputs and check invariants. For the rate limiter, a unit test checks "5 requests pass, 6th fails." The property test checks "for any limit L and any overflow k, exactly L pass and exactly k fail" — across hundreds of random (L, k) pairs. It found a bug in my initial backoff implementation where jitter could occasionally produce a value slightly below the base, violating the lower bound.

**Q: How does the content redactor prevent data leakage?**
A: Before calling the external A2A API, `redact_request` does three things: truncates `internal_status` to 500 chars (prevents large chunks from slipping through as "summaries"), replaces SHA-256 fingerprints of known chunks with `[REDACTED]`, and raises `ContentLeakError` if any verbatim 50-char prefix of a stored chunk appears in the question field. The last check is the strongest — if the orchestrator accidentally included chunk text in the question, delegation is aborted entirely.

**Q: How would you scale this to production?**
A: Several things would need to change. The `BaseHTTPRequestHandler` would be replaced with an async framework (FastAPI + uvicorn). The `InMemoryAuditSink` would be backed by the `audit_logs` PostgreSQL table. The `ConnectionPool` and `QueryCache` are already designed for injection — you'd swap in Redis for the cache and a proper connection pool for multi-process deployments. The `BackgroundWorker` would be replaced with a proper job queue (Celery + Redis, or AWS SQS). Authentication would use a real JWT issuer rather than a shared secret.

---

## 6. Honest Limitations

**Not production-ready because:**
1. **No real embedding model** — `HashingQueryEmbeddingProvider` produces meaningless vectors. Retrieval results will not be semantically relevant without a real embedding API.
2. **No live transcription** — Whisper must be installed separately; it's not bundled or tested with real audio files.
3. **Single-process only** — The `InMemoryAuditSink`, `SlidingWindowRateLimiter`, and `QueryCache` are not shared across processes. Scaling to multiple workers requires external state.
4. **No real auth issuer** — JWT is verified correctly, but there's no user management, no token refresh, and no `/sign-in` page with real credentials.
5. **Untested against real pgvector** — All database-dependent tests use mocks. The SQL is correct but the HNSW configuration has not been benchmarked against a real corpus, and `BatchEmbedder` is not yet wired into the Postgres ingestion path (only the in-memory path persists end-to-end). The in-memory retrieval path, however, is exercised by real (non-mock) code and the seed script.
6. **No HTTPS** — The backend serves plain HTTP. In production, TLS termination would happen at a load balancer.

**The tests are real, the architecture decisions are justified, and the code quality is production-grade — but it's a portfolio demonstration, not a shipped product.**
