# Omni-Modal Enterprise Research Orchestrator

A multi-tenant enterprise research platform that ingests PDF and audio documents, stores them as vector embeddings, and answers research queries using an internal retrieval pipeline optionally augmented by external AI delegation (A2A / Gemini Interactions API). Built across 12 phases as a full-stack portfolio project.

---

## What This Project Demonstrates

- **Full-stack architecture** — Next.js 16 App Router frontend + Python `BaseHTTPRequestHandler` backend in a monorepo
- **Vector search** — pgvector cosine-similarity retrieval with HNSW index tuning
- **Observability** — Sentry distributed tracing across frontend and backend, property-based tests for correctness
- **Enterprise security** — JWT authentication, RBAC, document-level access control, PII scrubbing, rate limiting, content redaction before external delegation
- **Performance** — async ingestion queue, connection pooling, query result caching, batch embedding writes
- **Test quality** — 306+ backend tests including 34+ Hypothesis property-based tests covering correctness invariants
- **Works locally without a database** — a Postgres-free in-memory path wires ingestion → persistence → retrieval end-to-end so the demo runs out of the box (with a deterministic, non-semantic embedder)

---

## Architecture

```
Browser (Next.js 16)
    │  sentry-trace + baggage + X-Correlation-ID
    ▼
Python HTTP Backend (BaseHTTPRequestHandler)
    │  Rate Limiter → JWT Auth → RBAC → Input Validator
    ▼
DeterministicAgentGraph
    ├── ValidateRequestNode
    ├── InternalRetrievalNode  ──→  PgVectorChunkRetriever  ──→  PostgreSQL + pgvector
    ├── MissingDataDetectionNode
    ├── ExternalDelegationNode  ──→  HttpA2AResearchClient  ──→  External AI API
    ├── ExternalEvidenceMergeNode
    ├── ReasoningSynthesisNode
    └── ControlledFallbackNode
```

**Key infrastructure:**

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS |
| Backend | Python 3.11, stdlib `http.server` |
| Database | PostgreSQL + pgvector extension |
| ORM / Schema | Drizzle ORM (TypeScript) |
| Observability | Sentry SDK (Python + `@sentry/nextjs`) |
| Testing | Python `unittest` + Hypothesis PBT |
| Monorepo | npm workspaces |

---

## Repository Layout

```
.
├── apps/web/               # Next.js 16 frontend
│   └── src/
│       ├── app/            # App Router pages (/, /documents, /research, /upload)
│       ├── components/     # UI components + Sentry error boundary
│       ├── hooks/          # validateUploadFile client-side hook
│       └── lib/            # api-client.ts, sentry.ts
├── packages/db/            # Drizzle schema + migrations
│   └── drizzle/
│       ├── 0001_initial.sql    # Schema: documents, document_chunks, embeddings, audit_logs
│       └── 0002_perf.sql       # HNSW tuning (m=16, ef_construction=64, ef_search=40)
├── services/api/
│   └── src/omni_modal/
│       ├── benchmark/      # python -m omni_modal.benchmark
│       ├── data_access/    # McpDataAccess protocol
│       ├── db/             # ConnectionPool singleton
│       ├── entity_extraction/  # QLoRA interface (stub)
│       ├── ingestion/      # PDF/audio pipeline, BatchEmbedder, AsyncIngestionQueue, BackgroundWorker
│       ├── mcp/            # MCP tool server (search_documents, get_document, search_chunks, get_entities, get_audit_logs)
│       ├── orchestration/  # DeterministicAgentGraph, A2A client, FallbackController
│       ├── qa/             # PgVectorChunkRetriever, QueryCache, synthesis
│       ├── security/       # JWT auth, RBAC, document access guard, rate limiter, redactor, audit sink
│       ├── config.py
│       ├── main.py         # HTTP server entrypoint
│       ├── observability.py
│       └── retry.py
└── docs/
    ├── architecture/
    └── security-boundaries.md
```

---

## Quickstart: Local End-to-End Demo (no paid services required)

This path uses the in-memory vector store (no Postgres) and the hashing embedder (no API key). Everything runs locally.

```bash
# 1. Install backend dependencies
cd services/api
pip install -e .
cd ../..

# 2. Generate a JWT signing secret (any random string works for local dev)
$env:JWT_SECRET = "$(python -c "import secrets; print(secrets.token_hex(32))")"

# 3. Start the backend
$env:JWT_SECRET = "your-secret-here"   # PowerShell — or: export JWT_SECRET=... on bash
python -m omni_modal.main              # http://localhost:8000

# 4. Issue a dev bearer token (same JWT_SECRET as above)
python scripts/issue_jwt.py --tenant demo-tenant --user u1 --roles researcher

# 5. Create apps/web/.env.local with these two lines:
#   NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
#   NEXT_PUBLIC_API_TOKEN=<token from step 4>

# 6. Start the frontend
npm install --legacy-peer-deps
npm run dev                            # http://localhost:3000
```

Then open `/upload` to ingest a PDF, then `/research` to query against it.

The system is fully functional with this setup. Nothing is mocked. Upload → ingest →
persist → retrieve → stream is wired end-to-end using the offline hashing embedder.

**To add real semantic embeddings without OpenAI (free, local):**
```bash
pip install sentence-transformers
$env:EMBEDDING_BACKEND = "sentence-transformers"
python -m omni_modal.main
```

**To add real semantic embeddings via OpenAI ($0.02/1M tokens):**
```bash
$env:EMBEDDING_BACKEND = "openai"
$env:OPENAI_API_KEY = "sk-..."
python -m omni_modal.main
```

**To verify retrieval quality with your active backend:**
```bash
python -m omni_modal.benchmark.embedding_compare
```

**To run a quick end-to-end retrieval test without starting the server:**
```bash
python scripts/seed_demo.py
```

---

## Running the Project

### Prerequisites

- Node.js 18+, Python 3.11+
- PostgreSQL with pgvector extension (for live DB features)

### Frontend

```bash
npm install
npm run dev          # Next.js dev server at http://localhost:3000
npm run typecheck:web
```

### Backend

```bash
cd services/api
pip install -e ".[db,observability,performance]"
python -m omni_modal.main   # HTTP server at http://localhost:8000
```

### Tests

```bash
# Backend unit + property-based tests (306 tests)
python -m unittest discover -s services/api/tests -p "test_*.py"

# HTTP integration tests (starts a real server on a random port)
python -m unittest discover -s services/api/tests/integration -v

# Frontend unit tests (Vitest — real assertions, not stubs)
npm run test:frontend

# Frontend typecheck
npm run typecheck:web

# Run everything
npm run validate
```

### Database

```bash
# Apply schema and pgvector extensions
npm run db:extensions
npm run db:migrate

# HNSW performance tuning (Phase 11)
# Apply packages/db/drizzle/0002_perf.sql manually or via psql
```

### Benchmark

```bash
python -m omni_modal.benchmark --queries 100 --docs 10 --output results.json
```

---

## Environment Variables

All variables are sourced from actual code — nothing is invented. See `.env.example` for full documentation including how to obtain each value.

### Legend
- **Required** — the system will not work without it
- **Required if** — required only when a specific feature is enabled
- **Optional** — has a safe default; omit to use the default
- **Interface only** — read by `/health` status only; not wired at runtime

### Backend (Python)

| Variable | Required? | Default | Purpose | Service |
|---|---|---|---|---|
| `JWT_SECRET` | **Required** | — | Signs/verifies JWT tokens. Generate: `openssl rand -hex 32` | Free, local |
| `DATABASE_URL` | Optional | — | pgvector Postgres connection string. **Unset = in-memory demo path.** | Free (Neon/Supabase free tier) |
| `DB_POOL_MIN` | Optional | `2` | Min psycopg_pool connections | — |
| `DB_POOL_MAX` | Optional | `10` | Max psycopg_pool connections | — |
| `ENVIRONMENT` | Optional | `development` | Sentry environment tag | — |
| `SENTRY_DSN` | Optional | — | Backend Sentry DSN. Unset = Sentry disabled. | Paid (free tier: 5k/mo) |
| `SENTRY_TRACES_SAMPLE_RATE` | Optional | `0.1` | Fraction of transactions to trace (0–1) | — |
| `QUERY_CACHE_ENABLED` | Optional | `true` | Set `false` to disable in-process query cache | — |
| `EMBEDDING_BACKEND` | Optional | `hashing` | `hashing` / `openai` / `sentence-transformers` | See below |
| `EMBEDDING_DIMENSIONS` | Optional | `1536` | Vector dimensions (must match pgvector schema if DB is used) | — |
| `OPENAI_API_KEY` | Required if `EMBEDDING_BACKEND=openai` | — | OpenAI embeddings API key | Paid (~$0.02/1M tokens) |
| `OPENAI_EMBEDDING_MODEL` | Optional | `text-embedding-3-small` | OpenAI model name | — |
| `OPENAI_BASE_URL` | Optional | `https://api.openai.com/v1` | Override for Azure or proxy | — |
| `SENTENCE_TRANSFORMERS_MODEL` | Optional | `all-MiniLM-L6-v2` | Local model for sentence-transformers backend | Free, local |
| `A2A_DELEGATION_ENDPOINT` | Optional | — | External A2A endpoint URL. Unset = delegation disabled. | External |
| `GEMINI_INTERACTIONS_ENDPOINT` | Optional | — | Alias for `A2A_DELEGATION_ENDPOINT` | External |
| `WHISPER_MODEL_PATH` | Optional | `base` | Whisper model size (`tiny`/`base`/`small`/`medium`) or path. Activates audio transcription. | Free, local |
| `QLORA_ENTITY_MODEL_PATH` | Optional | `dslim/bert-base-NER` | HuggingFace NER model ID. Set to empty for rule-based only. | Free, local (~420 MB) |
| `ADK_PROJECT_ID` | Optional | `omero-local-dev` | Deployment identifier in health/observability | Local |

### Frontend (Next.js — `apps/web/.env.local`)

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `NEXT_PUBLIC_BACKEND_URL` | Optional | `http://localhost:8000` | Backend origin the browser calls |
| `NEXT_PUBLIC_API_TOKEN` | Required for browser UI | — | Dev bearer token. Generate: `python scripts/issue_jwt.py` |
| `NEXT_PUBLIC_SENTRY_DSN` | Optional | — | Frontend Sentry DSN. Unset = Sentry disabled. Set to the same DSN as `SENTRY_DSN`. |
| `NEXT_PUBLIC_SENTRY_ENVIRONMENT` | Optional | `development` | Sentry environment tag |
| `NEXT_PUBLIC_APP_NAME` | Optional | `Omni-Modal Enterprise Research Orchestrator` | Browser tab title |
| `BACKEND_BASE_URL` | Optional | `http://localhost:8000` | Backend URL for SSR (server-side only, not sent to browser) |
| `SENTRY_DSN` | Optional | — | Backend DSN presence check for server health helper |

---

## Embedding Backends (pluggable)

The embedding provider is selected at startup from `EMBEDDING_BACKEND` and is
shared by both ingestion (persistence) and retrieval, so vectors are always
comparable. If a real backend is requested but unavailable (missing key,
missing library, init error), the system **falls back to hashing and logs why**
— it never crashes, and the local demo keeps working.

| Backend | Status | Semantic? | Dims | Notes |
|---|---|---|---|---|
| `hashing` (default) | ✅ Verified working / fallback | No | 1536 | Deterministic bag-of-words. Offline, zero deps. Ranks by token overlap, not meaning. |
| `openai` | ✅ Production-ready path (needs key) | Yes | 1536 | `text-embedding-3-small` via REST (stdlib `urllib`, no new deps). 1536 dims → compatible with **both** in-memory and pgvector paths. Verified via mocked-HTTP tests; live quality not benchmarked here. |
| `sentence-transformers` | 🧪 Experimental / local | Yes | 384 (model-dependent) | Fully offline after model download. Optional dep (`pip install sentence-transformers`). 384 dims work with the in-memory path; pgvector’s `vector(1536)` column would need a 1536-dim model or a schema change. |

Switch backends with no code changes:

```bash
# Real semantic embeddings (OpenAI)
EMBEDDING_BACKEND=openai  OPENAI_API_KEY=sk-...  python -m omni_modal.main

# Local semantic embeddings (no API key)
pip install sentence-transformers
EMBEDDING_BACKEND=sentence-transformers  python -m omni_modal.main
```

### Measured retrieval quality (real numbers)

`python -m omni_modal.benchmark.embedding_compare` computes recall@1 / MRR live
on a 5-doc / 5-query paraphrase set where queries deliberately share few surface
tokens with their target document:

| Backend | recall@1 | MRR | How measured |
|---|---|---|---|
| `hashing` (fallback) | **0.40** | **0.70** | Live run, no network |
| semantic (concept stub, in tests) | **1.00** | **1.00** | `test_embedding_providers.py` |

The hashing→semantic gap is real and reproducible. Numbers for `openai` /
`sentence-transformers` are intentionally **not** published here because they
have not been benchmarked on this machine — run the script with a real backend
configured to produce them.

---

## Troubleshooting

**API calls fail in the browser with CORS errors**
The backend adds `Access-Control-Allow-Origin` headers for `http://localhost:3000` by default. If you're running the frontend on a different port, set `NEXT_PUBLIC_BACKEND_URL` to match, and the backend will echo your origin back in CORS headers. The backend also handles `OPTIONS` preflight requests.

**Upload goes to "failed" immediately**
Check that the file is a PDF or WAV/MP3. MIME sniffing rejects unrecognized types. For audio, Whisper CLI must be on PATH (`whisper --help` should work). Set `WHISPER_MODEL_PATH=base` in `.env`.

**Research returns "no data" after upload**
Ingestion runs asynchronously — wait until the upload queue shows "ready" status before querying. Poll `GET /ingest/jobs/:id` or refresh the upload page.

**NEXT_PUBLIC_API_TOKEN is not set**
Run `python scripts/issue_jwt.py --tenant demo-tenant --user u1 --roles researcher,admin` and paste the token into `apps/web/.env.local` as `NEXT_PUBLIC_API_TOKEN=<token>`.

**dslim/bert-base-NER download fails**
Run `python scripts/download_models.py` to pre-fetch models with better error output. If behind a proxy, set `TRANSFORMERS_OFFLINE=1` and download manually.

---

## Deployment

### Docker (backend)

```bash
docker compose up --build   # starts the Python API at http://localhost:8000
```

The backend `Dockerfile` uses Python 3.11 slim, runs as a non-root user, and includes ffmpeg for Whisper audio processing. The `HOST=0.0.0.0` and `PORT=8000` environment variables control the bind address.

### Vercel (frontend)

The `apps/web/vercel.json` configures the Next.js frontend for Vercel deployment.

1. Install the [Vercel CLI](https://vercel.com/docs/cli): `npm i -g vercel`
2. Set environment variables in your Vercel project (all `NEXT_PUBLIC_*` vars + `SENTRY_DSN`)
3. Deploy: `vercel --cwd apps/web`

Or connect the GitHub repo to Vercel for automatic deploys on push.

### Production considerations

- Replace `InMemoryAuditSink` with a PostgreSQL-backed sink (the `audit_logs` table exists and is ready)
- The in-process `SlidingWindowRateLimiter` and `QueryCache` are not shared across processes — use Redis for multi-worker deployments
- JWT auth uses a shared secret — replace with a real IdP (Auth0, Okta, NextAuth) for production
- The `BackgroundWorker` daemon thread should be replaced with Celery + Redis for horizontal scaling

---

## Key Design Decisions

**Why `BaseHTTPRequestHandler` instead of FastAPI/Flask?**
The backend deliberately avoids web frameworks to demonstrate understanding of HTTP at the stdlib level — socket handling, header parsing, streaming SSE responses. It's more educational and shows low-level knowledge.

**Why Hypothesis property-based testing?**
Unit tests catch known cases; property tests find unknown edge cases. The 34+ PBT tests cover correctness invariants: JWT round-trips, PII scrubbing completeness, rate limiter window boundaries, batch partitioning, cache eviction, etc.

**Why in-process caching and queuing?**
The design avoids external dependencies (no Redis, no Celery) to keep the deployment model simple and the architecture understandable. These components are injected via constructor, so swapping in Redis or a real message queue requires only a protocol-compatible class.

**Why content redaction before A2A delegation?**
Internal document chunks must never leave the tenant security boundary. The `Redactor` intercepts delegation payloads, truncates `internal_status` to 500 chars, replaces chunk fingerprints with `[REDACTED]`, and raises `ContentLeakError` if verbatim chunk content is detected in the question field.

---

## What Is and Is Not Implemented

### Implemented ✅

- Next.js 16 App Router frontend with routing, Tailwind CSS, Sentry error boundary, upload validation
- **Frontend wired to the backend**: `/upload` sends files (base64) to `/ingest/upload` and polls job status; `/research` streams answers from `/query/stream` (real SSE parsing) and renders citations
- **End-to-end local path (no DB)**: `/ingest/upload` → background worker → `InMemoryChunkPersistence` → `InMemoryVectorStore` → `InMemoryChunkRetriever` returns ranked cosine-similarity results
- Python HTTP backend: ingestion pipeline, vector retrieval, MCP tool server, ADK workflow orchestration
- **All pages wired to live backend data**: Dashboard (`/health`), Documents (`/documents` + `/entities`), Projects (`/projects`), Archives (`/archives`), Settings (`/health`)
- **Startup log**: backend prints active path (in-memory vs pgvector), embedding backend, Whisper model, NER model, and Sentry status on startup
- pgvector schema with HNSW index and covering index (SQL migration files)
- Sentry distributed tracing: frontend→backend via `sentry-trace`/`baggage` headers, child spans, PII scrubbing
- Retry decorator with exponential backoff, jitter, Retry-After support, and retryable classifier
- FallbackController: graceful degradation on delegation/retrieval/tool failures
- JWT authentication (HS256, constant-time signature verification); `scripts/issue_jwt.py` issues dev tokens
- RBAC on HTTP endpoints (`researcher`, `admin`, `auditor` roles)
- Document-level access control (`private`, `tenant`, `restricted` visibility), enforced in the MCP CLI via `DocumentAccessGuard`
- Input validation, upload safety (MIME sniffing with fallback), content redactor
- Sliding-window rate limiter (per-tenant 60/min, per-user 20/min, delegation 10/hr)
- InMemoryAuditSink with monotonic IDs and argument scrubbing
- ConnectionPool singleton (`psycopg_pool`, double-checked locking)
- QueryCache (LRU + TTL via `cachetools`, tenant eviction secondary index)
- AsyncIngestionQueue with BackgroundWorker daemon thread and watchdog restart
- Benchmark harness (`python -m omni_modal.benchmark`, atomic JSON output)
- 306+ tests: unit, integration, Hypothesis property-based, and 12 real frontend tests (Vitest)

### Not Implemented (Stubs / Interfaces Only) ⚠️

- **Whisper transcription** — `LocalWhisperTranscriber` is wired. Set `WHISPER_MODEL_PATH=base` (or any model size) to activate. Requires Whisper CLI on PATH (`pip install openai-whisper`).
- **Entity extraction** — `EntityExtractionService` runs automatically post-ingestion. Rule-based by default; set `QLORA_ENTITY_MODEL_PATH=dslim/bert-base-NER` for real HF NER (free, ~420 MB). Entities are exposed via `GET /entities/:document_id`.
- **Semantic embeddings are now pluggable** — `EMBEDDING_BACKEND` selects `hashing` (default offline fallback), `openai` (real, production-ready, 1536-dim, works with in-memory + pgvector), or `sentence-transformers` (real local, experimental, 384-dim, in-memory). The default remains the deterministic hashing fallback so the offline demo needs no keys; real backends are opt-in. Live semantic quality has not been benchmarked on this machine — see the embedding benchmark to produce real numbers.
- **pgvector ingestion persistence** — `BatchEmbedder` (correct `ON CONFLICT DO UPDATE` upsert, single transaction, proper pgvector literal) is built and unit-tested but is **not yet wired into the Postgres ingestion path** (document-row creation is out of scope). Only the in-memory path persists end-to-end today. The pgvector retriever reads whatever has been seeded into the DB.
- **Measured benchmark numbers** — the HNSW config in `0002_perf.sql` has not been benchmarked against a real corpus. Run `python -m omni_modal.benchmark` against a populated DB to produce real recall/latency figures before citing any.
- **QLoRA entity extraction** — Interface and training scaffolding exist; no model weights are included.
- **Live A2A / Gemini delegation** — `HttpA2AResearchClient` implements the JSON-RPC protocol correctly but requires a real endpoint URL. `DisabledExternalResearchClient` is used by default.
- **Persistent audit log** — `InMemoryAuditSink` is in-process. A PostgreSQL-backed sink would implement the same `EnhancedAuditSink` protocol.
- **Frontend authentication UI** — `middleware.ts` is a pass-through; there is a `/sign-in` page that explains how to generate a dev token. JWT is enforced at the backend API level.
- **Real session management** — JWT is enforced at the backend (HS256, constant-time, expiry checked). The frontend sends the `NEXT_PUBLIC_API_TOKEN` bearer token on every API call. Production would use cookie-based sessions (NextAuth etc.).
- **Live database for most tests** — DB-dependent tests use mocks. Integration tests against a real pgvector instance require `DATABASE_URL` to be set.

---

## Phases Completed

| Phase | Description | Outcome |
|-------|-------------|---------|
| 1 | Foundation scaffold | Next.js shell, Python backend, Drizzle schema, security boundary docs |
| 2–8 | Core system | Ingestion pipeline, MCP tools, vector retrieval, ADK workflow, A2A delegation |
| 9 | Observability & Recovery | Sentry full-stack tracing, retry/fallback, 117 passing tests |
| 10 | Enterprise Security | JWT, RBAC, document access control, rate limiting, redaction, 230 passing tests |
| 11 | Performance & Scalability | Async ingestion, connection pool, query cache, batch writes, HNSW tuning, 275 passing tests |
| 12 | Portfolio packaging | This README, resume bullets, interview script |

---

## License

Private portfolio project. Not licensed for redistribution.
