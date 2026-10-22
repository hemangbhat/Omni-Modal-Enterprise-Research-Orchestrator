# OMERO — interview guide

How to present OMERO in a technical interview: the pitch, a demo you can run, the stories worth telling, the numbers you can defend, and the questions you will get. Every figure here comes from this repository and can be reproduced with the command next to it. If you cannot reproduce a number on the day, don't quote it.

---

## 1. The 30-second pitch

> "OMERO is a multi-tenant research workspace. A team uploads PDFs and recordings. Then they ask questions, and every answer cites the exact page or moment in the recording it came from. If the documents don't contain the answer, it says so instead of guessing. It's a FastAPI backend and a Next.js frontend on Postgres with pgvector. I measured the retrieval: adding keyword search to vector search raised top-1 accuracy from 59% to 77% on a labelled set I built. And I proved tenant isolation with a test that attacks every endpoint."

## 2. The 2-minute story

1. **Problem.** Teams keep answers in documents and meetings, and a chatbot that makes things up is worse than none. The product rule: *answer only from the user's evidence, show the evidence, and say when it isn't there.*
2. **Pipeline.** An upload goes straight to object storage through a presigned URL. A durable job record in Postgres moves it through extracting, transcribing, chunking, embedding and indexing. Questions run through a fixed workflow: retrieve, synthesise with a model (Groq, then Gemini, then a no-model extractive fallback), check the citations, and only then optionally search the web.
3. **Proof, not claims.** I built a labelled evaluation corpus and measured vector-only against hybrid retrieval. Hybrid won, so it became the default. A cross-tenant test hands one tenant's ids to another on every route that takes an id. A restart test checks that data survives a process restart on real Postgres. And a browser test runs the whole journey: sign up, upload, ask, save, report.
4. **What the tests caught.** Writing those tests found real bugs: a migration that could never run on a fresh database, a rate limit that locked out new users within a minute, and a query cache that was never written to. That's the part I'm proudest of.

---

## 3. What runs where (be precise about this)

| | Status |
|---|---|
| Local, no keys, no database | Everything works: in-memory stores, hashing embeddings, extractive answers. This is the demo to show. |
| Local with Postgres | All 15 migrations apply to an empty pgvector database. The restart and Postgres tests pass against a throwaway container. |
| Deployed (Vercel + Render + Neon + S3) | **Live** at https://omni-modal-enterprise-research-orch.vercel.app with the latest code: sign-up, upload to S3, cited AI answers (Groq), projects and reports checked end to end on 2026-10-19. The API sleeps when idle on Render's free tier, so open the site a minute before the interview to wake it. |
| Web research | Built and tested with a fake provider. Needs a free Tavily key to run for real. |

---

## 4. Architecture (whiteboard version)

```
 Browser (Next.js 16, React 19)
   │  JWT access token (+ rotating refresh token), SSE for answers
   ▼
 FastAPI ─ auth → rate limits (general + "heavy") → role check → handler
   │
   ├─ Upload:   POST /documents/uploads → presigned S3 POST (size-capped)
   │            → POST /documents/{id}/complete → ingestion job (Postgres)
   │            → extract (PDF) / transcribe (Groq Whisper) → chunk → embed → index
   │
   ├─ Research: POST /query/stream (SSE)
   │            ValidateRequest → InternalRetrieval (hybrid: pgvector + full-text, RRF)
   │            → Synthesis (Groq → Gemini → extractive) → citation check
   │            → WebResearch (only if no evidence AND workspace allows) → Fallback
   │
   └─ Product:  sessions, projects, findings, reports, library, dashboard, billing (Stripe)
   ▼
 Postgres + pgvector (Neon)   ·   S3 (originals)   ·   Redis (optional: cache, limits, queue)
```

**One request, end to end** (practise saying this):
1. The browser sends the question with the session id. FastAPI checks the JWT, applies the per-user and per-tenant limits, and opens an SSE stream.
2. A follow-up is rewritten into a standalone search query using earlier turns.
3. Retrieval runs a pgvector cosine search and a Postgres full-text search, both filtered by tenant, ready status, not archived, and the project scope if there is one. It merges them with reciprocal rank fusion.
4. The model gets numbered passages and must cite `[n]`. Its tokens stream to the browser as they arrive.
5. The finished answer is checked. Citation numbers that don't match a passage are removed, and each citation gets its title, page or time range, and the supporting sentence.
6. If the answer is "not enough evidence" and the workspace allows it, a redacted question goes to web search. Results come back labelled "From the web", with links.
7. The message and its citations are saved to the session.

---

## 5. Demo script (6–7 minutes, local, no keys)

Before the interview, run it once start to finish. Have a short PDF ready (a contract or meeting minutes).

```bash
# Terminal 1: API (in memory, ignores your .env)
cd services/api/src
OMERO_DISABLE_DOTENV=1 JWT_SECRET=demo-secret python -m omni_modal.api
```

```bash
# Terminal 2: frontend (apps/web/.env.local has NEXT_PUBLIC_BACKEND_URL=http://localhost:8000)
npm run dev
```

1. **Sign up** at `/sign-up`. Point at the dashboard's "Getting started" checklist. It ticks from real progress, not a script.
2. **Upload** the PDF at `/upload`. Narrate the stages as they light up: *"The file went straight to storage; this is a durable job in the database, so a restart picks it up again."*
3. **Create a project** and add the document. Click *Research to find more*.
4. **Ask** a question the PDF answers. Click a citation chip, and the evidence card shows the exact sentence and page.
5. **Ask** something the PDF can't answer. It says so. *"No guessing is a product rule, enforced in code, not in the prompt alone."*
6. **Save** the good answer to the project, then **Generate report**. *"The report is compiled from saved findings with one renumbered source list. No model writes it, so it can't invent anything."*
7. **Show proof** (pick one): `npm run test:e2e` (the same journey in a real browser), or the eval table in the README.

If something breaks live, say what it is and move on to the code. Interviewers care more about how you handle that than about the failure.

---

## 6. Stories worth telling (problem → decision → evidence)

Use the STAR shape. Each story has files to open if they ask "show me".

### 6.1 Measuring retrieval instead of assuming
- **Problem:** Deployment runs on Render's free tier (512 MB), so no local embedding model fits. The fallback "hashing" embedder only matches words.
- **Decision:** Build hybrid retrieval: BM25 in memory and Postgres full-text in production, next to vector search, merged with reciprocal rank fusion (k = 60). It only becomes the default if it measurably helps.
- **Evidence:** A labelled corpus with 32 documents (including deliberate near-misses) and 56 questions. Hashing: recall@1 0.589 → 0.768, MRR 0.681 → 0.839. MiniLM: recall@1 0.786 → 0.821, but recall@5 fell by one question, and I report that too. CI fails if a score drops by more than 0.02.
- **Files:** `qa/lexical.py`, `qa/retrieval.py`, `benchmark/eval.py`, `eval/corpus.json`.
- **Reproduce:** `cd services/api && python -m omni_modal.benchmark.eval --backends hashing,sentence-transformers`

### 6.2 Citations you can trust
- Models cite `[n]`. The server validates every marker against the retrieved set and removes the rest, so a hallucinated `[7]` can't reach the user. Runs like `[1][2]` are read as one group. Each citation carries the sentence that best supports the claim it's attached to.
- The eval also measures *supported citations* (the quoted snippet contains the fact): 0.71 on the deployed setup. It's lower than recall because without a model the sentence is chosen by word overlap. **Say this before they find it.**
- **Files:** `qa/citations.py`, `qa/grounded.py`.

### 6.3 Durable ingestion on a free tier
- Render's free tier has no background worker and sleeps when idle. So the job record lives in Postgres, jobs are claimed atomically (`UPDATE … WHERE stage = 'queued'`, so only one worker wins), a sweeper retries stuck jobs with backoff, and unfinished jobs resume on startup. "Done" is written only after chunks and embeddings are committed.
- Uploads skip the API entirely: a presigned S3 POST with a size limit writes to an `.incoming` key, which is moved into place on completion. Re-uploading the same bytes is recognised by content hash.
- **Files:** `documents/service.py`, `documents/store.py`, `saas/adapters/storage.py`.

### 6.4 Proving tenant isolation
- Every query filters by `tenant_id`; Postgres row-level security is available as defence in depth (`RLS_ENFORCEMENT`).
- The interesting part is the test. Tenant A creates one of everything. Tenant B calls **all 30 routes that take an id** with A's ids and must get 403/404 with none of A's text. Then B checks every list and search. A coverage check fails the build if someone adds an id route without adding it to the test. The same test runs against real Postgres in CI.
- It found one real inconsistency: `/entities/{id}` answered 200 (with no data) for another tenant's id. It now answers 404.
- **Files:** `tests/test_cross_tenant_routes.py`, `db/rls.py`, `packages/db/drizzle/0008_rls.sql`, `0011_rls_empty_guc.sql`.

### 6.5 Bugs the proof work found (good for "tell me about a hard bug")
- **Migrations could never build a fresh database.** `0002_perf.sql` used `ALTER DATABASE current_database()`, which isn't valid SQL. Production only worked because those files had been marked as applied. Found by applying all migrations to an empty pgvector container in CI; fixed with a `DO` block.
- **New users were locked out within a minute.** The limit was 20 requests a minute per user on *every* request, and one dashboard load makes several. The browser test hit it. I split it in two: a generous limit for browsing, and the strict one for questions, the assistant and uploads.
- **A cache that never cached.** The pgvector retriever read the query cache but never wrote to it.

### 6.6 Web research without leaking data
- It runs only when the documents can't answer, the workspace admin has turned it on, the plan includes it, and the hourly and monthly limits allow it.
- Only the question leaves, after removing emails, phone numbers, links, long numbers, and any figure or four-word phrase that appears in the retrieved passages. If too little is left, nothing is sent. The answer shows the exact search text.
- Results sit under their own heading, labelled "From the web", with links, never merged into internal evidence.
- Provider choice: Gemini search grounding was the plan, but it isn't free on current models, so I chose Tavily (free tier) and documented why.
- **Files:** `research/external.py`, `orchestration/graph_nodes.py` (`WebResearchNode`).

### 6.7 Billing that survives Stripe's delivery model
- Stripe delivers events at least once and out of order. Applied event ids are logged, so duplicates are ignored. A plan event older than the organisation's last applied one is ignored, so a late "upgrade" can't undo a cancellation. An event is recorded only after it's applied, so a failure leaves it for Stripe's retry.
- **Files:** `saas/billing_events.py`, `tests/test_billing_lifecycle.py`.

---

## 7. Numbers you can quote

| Claim | Source / command |
|---|---|
| 776 backend tests, 137 frontend tests, 32 Playwright checks | `python -m pytest tests` · `npm run test:frontend` · `npm run test:e2e` |
| 8 Postgres-only tests (migrations, restart, isolation) | `OMERO_TEST_DATABASE_URL=… pytest tests/test_postgres_*.py tests/test_cross_tenant_routes.py` |
| Hybrid retrieval: recall@1 0.589 → 0.768 (hashing) | `python -m omni_modal.benchmark.eval` |
| 30 id routes covered by the cross-tenant test | `tests/test_cross_tenant_routes.py` |
| 15 pages × 2 widths, no WCAG 2 A/AA violations (axe-core), no sideways scroll | `e2e/pages.spec.ts` |
| 15 SQL migrations, all applied to an empty database in CI | `.github/workflows/ci.yml` |

**Don't quote** latency or throughput figures. None were measured on the deployed stack.

---

## 8. Decisions and trade-offs (expect "why not X?")

| Decision | Why | What you gave up |
|---|---|---|
| Custom auth (JWT access + rotating refresh with reuse detection, TOTP two-step, password reset) instead of Clerk | Already built and tested; migrating adds risk and no demo value | Social login, hosted UI |
| A fixed workflow, not an agent framework | The steps are known; a fixed order is testable and can't wander | Flexible multi-step tool use |
| Postgres for everything (vectors, full-text, jobs, events) | One system to back up, transactions across all of it, a free tier | Specialised vector DB features at large scale |
| Reports compiled, not generated | They can't invent anything; every sentence comes from saved material | Prose quality |
| Extractive fallback when no model key | The product still works and stays honest offline | Answer quality without a model |
| Keyword search as well as vectors | Names, codes and figures beat embeddings; measured win | A second query per question |
| Web research off by default, per workspace | It's a data-leaving action; admins opt in | Convenience |

---

## 9. Questions you'll likely get

**How do you stop the model from making things up?**
Three layers. The prompt says to answer only from numbered passages. The server removes any citation that doesn't match a passage. And if the model says the passages don't answer, the response becomes an explicit "not enough evidence" with no citations. Without a model, a passage that shares no content word with the question isn't quoted at all.

**How would you scale it?**
The web tier is already stateless when `REDIS_URL` is set (shared cache, rate limits, queue). Next steps: move ingestion to dedicated workers (the job table already supports atomic claims across processes), put the ANN index on a larger instance or partition by tenant, add a reranker behind the existing setting *if the eval shows it helps*, and add read replicas for list pages.

**Why hybrid search?**
Because I measured it. Then explain reciprocal rank fusion: it merges rankings without calibrating scores across two different systems.

**What happens if the backend restarts mid-upload?**
The file is already in S3 under an `.incoming` key and the job is in Postgres. On startup, unfinished jobs resume. The restart test checks this kind of survival in a real second process.

**How is one tenant's data protected from another?**
Tenant filters in every query, RLS available as a second layer, the cache key includes the tenant, and the cross-tenant test covers every id route, including on Postgres in CI.

**What would you do differently?**
Start the labelled evaluation and the cross-tenant test on day one. They found more real bugs than anything else.

**What's the weakest part?**
The no-model snippet choice ("supported citation" 0.71), and the hashing embedder in deployment. The fix is a hosted embedding model and better sentence selection, both measurable with the existing eval.

---

## 10. Honest limitations

- Render's free tier sleeps when idle; the first request after a pause takes up to a minute.
- Deployment uses hashing embeddings (word overlap) because of the free tier's memory limit; hybrid search compensates and is measured.
- There's no reranker, and web research hasn't been run against the real provider yet.
- The SaaS tables are missing from the Drizzle schema file (the SQL migrations are the source of truth).
- Some plan flags (SSO, audit export, priority ingest) are listed in the plan table but not implemented. The pricing page shows only what's enforced.
- The browser tests cover the main journey and an accessibility pass, not every interaction.

Saying these first, plainly, is a strength. It shows you know the system better than a reviewer skimming it.

---

## 11. Resume bullets

- Built a multi-tenant research platform (FastAPI, Next.js 16, Postgres/pgvector, S3) that answers questions from uploaded PDFs and recordings with server-validated citations, and refuses to answer when the evidence isn't there.
- Added hybrid retrieval (pgvector + Postgres full-text, reciprocal rank fusion) and proved it on a labelled 56-question corpus: top-1 accuracy 59% → 77%, MRR 0.68 → 0.84, with a CI regression gate.
- Designed durable ingestion for a free-tier host: presigned S3 uploads, Postgres job records with atomic claims, retries with backoff, and resume on restart; verified by a two-process restart test on real Postgres.
- Proved tenant isolation with a test that attacks all 30 id-taking routes, plus a coverage check that fails on new uncovered routes; it runs in CI against a pgvector service container and caught a route that answered other tenants' ids with 200 instead of 404.
- 776 backend, 137 frontend and 32 Playwright tests, including axe-core accessibility checks at desktop and phone width; the tests found and fixed a broken migration, a lock-out rate limit and a dead cache.

---

## 12. Know these files cold

| If asked about | Open |
|---|---|
| The answer workflow | `services/api/src/omni_modal/orchestration/graph_nodes.py` |
| Retrieval and hybrid | `qa/retrieval.py`, `qa/lexical.py`, `qa/in_memory_store.py` |
| Citations | `qa/citations.py`, `qa/grounded.py` |
| Ingestion | `documents/service.py`, `documents/store.py` |
| Routes, auth, limits | `api/app.py`, `security/` |
| Projects and reports | `projects/service.py`, `projects/reports.py` |
| Web research | `research/external.py` |
| Proof | `tests/test_cross_tenant_routes.py`, `tests/test_postgres_restart.py`, `benchmark/eval.py`, `apps/web/e2e/` |

Be ready to explain any line of these in your own words. If you used AI tools while building, say so plainly and show that you understand and verified the code. The tests and the eval are how you verified it.
