# OMERO completion — design

Date: 2026-10-11
Status: approved by the owner on 2026-10-11 with two amendments: the full journey must work on the deployed site, and every checklist item in the vision is to be implemented regardless of how the owner had ticked it.
Source of intent: the owner's "OMERO — Complete Product Vision & Expectations" document (referred to below as "the vision").

## 1. Intent

OMERO is a portfolio project the owner will demo to recruiters and defend in system-design interviews. The vision's standard is: every major page has a working purpose, every important data flow is persistent and testable, every AI answer has traceable evidence, every sensitive operation has an authorization boundary, and every resume claim can be demonstrated live.

The owner also wants an AI assistant available across the whole site, "like newer websites have", not only on the Research page.

The codebase is healthy (417 backend tests and 26 frontend tests pass, TypeScript is clean) but a large part of the vision is not built, and some screens claim things the code does not do. This design closes that gap in ordered milestones, each of which leaves the product demoable.

## 2. Where the code stands today

Audited against the vision's sections on 2026-10-11. "Checked in the vision" means the owner's checklist marks it done.

| Area | Real today | Missing or misleading |
|---|---|---|
| A. Auth and orgs | Register, login, refresh rotation, logout, roles, invites, workspaces, Postgres stores | No password reset anywhere (checked in the vision). MFA is uncommitted, backend-only, untested, and has no UI |
| B. Ingestion and storage | PDF and audio extraction, chunking, embeddings, Redis queue with retries, upload validation | Upload sends the whole file as base64 inside JSON and writes it to the temp dir. The S3 adapter and its presign method exist but the upload route does not use them. No document row until processing finishes. Job state lives only in Redis or memory. No delete, no reprocess, no idempotency |
| C. Research | pgvector retrieval, LLM synthesis (Groq, then Gemini, then extractive), citations, redaction, query cache | Streaming is simulated: the full answer is generated, then sliced. No follow-ups: each question is stateless. History is browser localStorage only. No full-text or hybrid search, no reranker (both checked in the vision). Page numbers and audio timestamps are captured at ingestion but the UI cites only a chunk index and shows no snippet. "Confidence: High" is hard-coded |
| D. Projects | Nothing persisted | `GET /projects` returns one fake "project" per document with hard-coded status and priority. No projects table, no create or edit, no notes, findings or reports |
| E. Documents and archives | Live document list, entity inspector | `GET /archives` is the document list relabelled with hard-coded "AES-256", "Internal" and "7 years" retention. No archive or restore, no pagination, sort, download, delete or reprocess |
| F. External research | A generic HTTP delegation client, redaction before delegation, fallback handling | No concrete provider is wired, so it is off unless an endpoint is supplied. Internal and external evidence are not labelled separately in the UI |
| G. Billing | Plans, usage metering with 402 on limit, Stripe checkout, verified webhook, portal, dunning | No pricing page. No billing history view |
| H. Notifications and observability | Resend and PostHog adapters, Sentry, correlation IDs, metrics endpoint, readiness probe, audit sink | No operational view of failed ingestion jobs. Readiness does not check storage or the model provider |
| I. Security and reliability | JWT, RBAC, document access guard, rate limiting, RLS behind a flag, security tests | Job history is lost on restart without Redis. No documented backup and recovery |
| J. Frontend | 14 pages, consistent visual design | Dead controls (attach and mic icons, some settings and sign-in buttons). Five frontend test files are empty stubs excluded from the run. No browser end-to-end test |

Other honesty gaps: the README describes the backend as stdlib `http.server` when it is FastAPI; the entity-extraction "QLoRA training" is an admitted stub; the deployed Render service uses a hashing embedder, which is not semantic.

## 3. Goals

1. The vision's eleven-step user journey (section 4 of the vision) works end to end on the deployed site (Vercel frontend, Render API, Neon Postgres, S3) and survives a backend restart. The local no-database demo keeps working too.
2. Every checklist item in sections A to J of the vision is implemented and tested. The owner's own tick marks are ignored; the audit in section 2 is the starting point. Claims that are not checklist items and are not true (for example QLoRA training) are removed.
3. A site-wide assistant that is grounded in the user's own documents and workspace data.
4. Evidence the owner can show: a browser end-to-end test, tenant-isolation tests, and measured retrieval quality.

## 4. Non-goals

- Migrating to Clerk. The custom auth is built and tested; replacing it adds risk and no demo value. (Builder's call, see section 8.)
- LangChain or LangGraph. The deterministic graph already fits; the vision says neither is mandatory.
- QLoRA fine-tuning. The stub is removed from product claims rather than implemented.
- Pinecone or any second vector store.
- Legal retention or legal-hold enforcement.

## 5. Anti-goals

These would make the work a failure even if it runs:

- A generic chat clone bolted onto a dashboard. The assistant must answer from the user's evidence and say so when evidence is missing.
- An assistant that can change or delete company data. It is read-only.
- Any screen showing invented numbers, labels or compliance claims.
- Published latency or accuracy figures that were not measured on this code.
- State that disappears on restart when a database is configured.

## 6. Constraints

- No new paid services. LLM stays Groq first, Gemini second, extractive last, behind the existing synthesizer contract.
- The no-database local demo path must keep working, with in-memory equivalents for every new store.
- The Render free tier has 512 MB of memory, so no local embedding, reranker or Whisper model can run in deployment. Deployed audio transcription uses Groq's hosted Whisper endpoint with the existing Groq key; deployed embeddings use a hosted embedding API. Local models remain an option for local runs.
- The Render free tier has no background worker and sleeps when idle. Ingestion therefore runs in the API process, the job record lives in Postgres, and unfinished jobs are picked up again on startup.
- Secrets are never committed and never pasted into chat. The owner places them in the root `.env` and in the Render and Vercel dashboards.
- Existing tests keep passing. New behaviour is written test-first.
- Schema changes are new numbered SQL migrations in `packages/db/drizzle`, matching the existing ones, with the Drizzle schema kept in step.
- Every new query path is tenant-scoped and covered by a cross-tenant test.
- Work happens on a feature branch per milestone, not on `main`.

## 7. Milestones

Order follows the vision's section 7. Each milestone ends with all tests green and a demoable product.

### M0 — Honest baseline

- Finish MFA: tests for enrol, confirm, login step-up, disable and replay; a code step on sign-in; an enrol screen in Settings. Confirm the changed `/auth/login` response still satisfies existing clients.
- Add password reset: request and confirm endpoints, single-use expiring token, email through the existing email adapter, two frontend screens.
- Remove invented claims: "Confidence: High", "AES-256", "7 years", "Internal" classification, fake project status and priority.
- Remove or wire every dead control.
- Delete the five empty frontend test stubs or replace them with real tests.
- Rewrite the README to match the code, and drop QLoRA from the feature claims.

### M1 — Research that holds up, and the site-wide assistant

Backend:
- Persist research: `research_sessions` and `research_messages` tables, with citations stored per message. Endpoints to list, open, rename and delete sessions.
- Follow-ups: a session's earlier turns are used to rewrite the new question into a standalone search query before retrieval, and are passed to the model as conversation history.
- True streaming: synthesizers gain a streaming method that yields model tokens as they arrive; `/query/stream` forwards them. The extractive fallback streams its finished text.
- Hybrid retrieval: a full-text index on chunk content, combined with vector results by reciprocal rank fusion. Kept only if the M4 evaluation shows it helps; otherwise it is removed, not left as a claim.
- Citations carry document title, page number or audio timestamp range, and the supporting snippet. Citation numbers the model emits are validated against the retrieved set; invalid ones are stripped.
- Insufficient evidence produces an explicit "not enough evidence" answer with no citations.

Site-wide assistant:
- A panel opened from a button present on every signed-in page, built on the same session and streaming engine.
- It receives the current page as context.
- It has read-only tools, each enforcing the caller's tenant and role: search documents, list documents and their processing status, usage and plan, team members, list projects, and "navigate to page".
- Tool results are treated as untrusted text. No tool writes.
- With no LLM key configured it states that plainly instead of failing silently.

Frontend:
- The Research page becomes a real multi-turn thread with a session list loaded from the server, evidence cards showing snippet and page or timestamp, and internal versus external labels.

### M2 — Durable ingestion and storage

- Upload flow: the client asks for an upload slot, receives a short-lived presigned URL, uploads straight to object storage, then confirms. Local development uses an S3-compatible container added to `docker-compose.yml`; with no object store configured, the local adapter serves an equivalent authenticated upload route.
- A document row is created at upload time and carries status through the whole pipeline.
- `ingestion_jobs` table in Postgres holding stage (uploaded, extracting, transcribing, chunking, embedding, indexing, done, failed), attempts and error. Redis remains the transport; Postgres is the record.
- "Done" is written only after chunks and embeddings are committed.
- Idempotency by content hash per tenant: re-uploading the same file returns the existing document.
- Delete (removes object, chunks, embeddings, entities, and evicts cache) and reprocess.
- Authorized download through a short-lived presigned URL.
- Upload page shows live stages and real error messages.

### M3 — The real product

- Projects: `projects` table plus links to documents, research sessions, notes and reports. Create, edit, archive, activity log, search within a project. Retrieval can be scoped to one project.
- Saved findings and notes attached to a project.
- Reports: generate a Markdown report from a session or a project's findings, with its citations; view and download.
- Decisions, risks, action items, owners and deadlines surfaced per project from the existing entity extraction.
- Documents page: server-side search, filter, sort and pagination; uploader, size, status and project; download, delete, reprocess.
- Archives: a real `archived_at` state with archive and restore, filters, and no retention claims.
- Dashboard built only from real counts and recent activity.
- Global search with a stated scope: document titles, projects and research sessions.
- Pricing page; billing history from Stripe when configured.
- Admin view of failed ingestion jobs with retry.
- Readiness probe also checks storage and the model provider.

### M4 — Proof

- Playwright end-to-end test of the vision's journey: sign up, upload, watch processing, ask, see a cited answer, save to a project, generate a report.
- Restart test: state created before a backend restart is present after it.
- Cross-tenant tests for every endpoint added in M1 to M3.
- Retrieval evaluation on a small labelled corpus committed to the repo, reporting recall@k and MRR for vector-only versus hybrid. Results go in the README as measured, with the command to reproduce them.
- Citation-correctness check on the same corpus.
- Billing tests for upgrade, downgrade, cancellation and webhook replay.
- CI runs all of the above that do not need external keys.
- A short backup and recovery section in `docs/operations.md`.

### M5 — External research and polish

- Decide per question whether internal evidence is enough; if not, and the workspace allows it, call one real external research provider behind the existing client interface.
- External findings are labelled as external, carry their own source links, and are recorded on the message.
- Only the redacted question leaves the system, never document content, and only when a workspace setting permits it. Timeouts, bounded retries and quota apply.
- Keyboard navigation, focus states, accessible labels, small-screen layout, and empty, loading and error states on every page.
- First-run guidance so a new user understands the product unaided.

## 8. Calls made by the designer, not the owner

The owner has not decided these. Each is a default the owner can overturn.

1. Keep the custom auth instead of moving to Clerk.
2. Keep Groq and Gemini as the LLM providers; add no new provider.
3. The site-wide assistant is read-only.
4. The assistant is built in M1 on top of the fixed research engine, not as a separate chat system.
5. Reports are Markdown, viewed in the app and downloadable. No PDF export.
6. The external research provider is Gemini with search grounding, because a Gemini key path already exists. The builder should confirm this is available on the free tier before committing to it, and fall back to a search API with a free tier if not.
7. Hybrid search and a reranker are both built, behind settings. The M4 evaluation decides which are switched on by default, and the README reports the measured result either way.
8. In deployment, semantic embeddings come from a hosted embedding API so the 512 MB limit holds. The builder should check what `embedding_factory.py` already supports before adding anything.
9. Milestones ship on separate branches, merged when green.

## 9. Left to the builder

Table and column shapes, endpoint names, component structure, the query-rewrite prompt, the fusion constant, and how tests are organised, all following the patterns already in the repo.

## 10. Owner's answers and what the owner supplies

Answers given on 2026-10-11:
- The full journey must work on the deployed site.
- Every checklist feature is wanted.
- No interview date was given, so milestones run in order.

Already present in the owner's local `.env` (values not inspected): Neon `DATABASE_URL`, Groq, Gemini, Resend, Stripe secret and publishable keys, S3 bucket and AWS credentials, Sentry.

Still needed from the owner, at the milestone shown:
- Now: the live frontend URL and live API URL. The API host written in `apps/web/vercel.json` (`omero-api.onrender.com`) answered 404 on 2026-10-11.
- Now: confirmation that commits on feature branches and pushes to GitHub are allowed, and whether merging to `main` triggers a deploy.
- M2: `REDIS_URL` from a free Upstash database; browser-upload CORS rules applied to the S3 bucket.
- M3: `STRIPE_WEBHOOK_SECRET`, created in the Stripe dashboard against the live API URL.
- Each milestone: the same variables entered in the Render and Vercel dashboards. The builder gives the owner an exact list each time.
