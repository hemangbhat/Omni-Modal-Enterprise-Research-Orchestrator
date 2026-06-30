# Operations & Production Runbook

This document covers running OMERO in production: database migrations, tenant
isolation, observability/SLOs, billing lifecycle, and the deployment-tier
upgrade path. It reflects what is implemented today and is explicit about what
is operational policy vs. code.

---

## 1. Database migrations (migrations as code)

SQL migrations live in `packages/db/drizzle/*.sql` and are applied by an
idempotent runner that records each file in a `schema_migrations` table.

```bash
# From the repo root, with DATABASE_URL set (or in .env):
python scripts/migrate.py --dry-run     # list pending migrations
python scripts/migrate.py               # apply pending migrations
```

The runner is safe to re-run: already-applied files are skipped. Discovery and
ordering are unit-tested (`tests/test_migrations_and_rls.py`).

### Adopting the runner on an existing database

If you previously applied `0001`–`0007` by hand (as on the current Neon
instance), baseline the tracking table so the runner doesn't replay them:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations (filename) VALUES
  ('0001_initial.sql'), ('0002_perf.sql'), ('0003_saas.sql'),
  ('0004_embeddings_384.sql'), ('0005_auth.sql'), ('0006_notifications.sql'),
  ('0007_audit_events.sql')
ON CONFLICT (filename) DO NOTHING;
```

Then `python scripts/migrate.py` will apply only the new `0008_rls.sql`.

### Where it runs

The runner and SQL files are at the repo root, which is **not** inside the
`services/api` Docker build context, so migrations are intended to run from CI
or locally against `DATABASE_URL` **before** new code is promoted — not as an
in-container step. A CI job (`migrate` before `deploy`) is the recommended wiring.

---

## 2. Tenant isolation — Row-Level Security (defense in depth)

Migration `0008_rls.sql` enables Postgres RLS on every tenant-scoped table. The
application already filters by `tenant_id`; RLS makes the **database** enforce
it too, so a single missing `WHERE` clause cannot leak across tenants.

- Policies key on a per-transaction GUC, `app.tenant_id`, set via
  `omni_modal.db.rls.set_tenant(conn, tenant_id)`
  (`SELECT set_config('app.tenant_id', $1, true)`).
- **Non-breaking rollout:** policies are permissive when the GUC is unset, so
  migrations, admin tooling, and the offline path keep working. When the app
  sets the GUC, isolation is enforced (including for the table owner — `FORCE`).
- **Strict mode:** once every tenant-scoped query path sets the GUC, remove the
  `current_setting(...) IS NULL` escape from each policy to fail closed.

Helper is unit-tested; wiring `set_tenant` into each pooled transaction is the
remaining step to make enforcement active (it is available but not yet forced on
every query path, to avoid destabilising the running app).

---

## 3. Observability & SLOs

### Signals
- **Errors + traces:** Sentry (frontend → backend via `sentry-trace`/`baggage`).
- **Metrics:** `GET /metrics` exposes Prometheus text — `http_requests_total`
  (counter) and `http_request_duration_seconds` (histogram), labelled by method,
  route template, and status. Scrape with Prometheus; chart in Grafana.
- **Structured logs:** one JSON line per request (method, route, status,
  `duration_ms`, `correlation_id`).
- **Correlation IDs:** every response carries `X-Correlation-ID` (honoured from
  the inbound header if present), tying frontend and backend logs together.

### Health probes
- `GET /health` — **liveness** (process is up).
- `GET /health/ready` — **readiness**: pings Postgres and Redis when configured;
  returns `503` if a configured dependency is down so load balancers route away.

### Suggested SLOs (targets, to be measured)
| SLO | Target | Source |
|-----|--------|--------|
| Availability | 99.5% monthly | uptime checks on `/health` |
| Query latency (p95) | < 500 ms | `http_request_duration_seconds{route="/query"}` |
| Ingestion success rate | > 99% | job status `ready` vs `failed` |
| Error rate | < 1% of requests | Sentry + `http_requests_total{status=~"5.."}` |

Alerting (Sentry alerts, Grafana rules) is operational configuration, not code.

---

## 4. Billing lifecycle (Stripe)

With `STRIPE_SECRET_KEY` set, `/billing` runs real (test-mode) Checkout + Portal
and syncs via signature-verified webhooks (`POST /billing/webhook`). Handled
events (`apply_webhook_event`):

| Event | Action |
|-------|--------|
| `checkout.session.completed` | Persist Stripe customer/subscription ids; apply purchased plan |
| `customer.subscription.updated` | Sync plan from subscription metadata |
| `customer.subscription.deleted` | Downgrade org to `free` |
| `invoice.payment_failed` | **Dunning** — notify the org owner to update billing |

Products/prices are auto-provisioned by stable `lookup_key` (no manual dashboard
setup). Plan limits are enforced server-side (HTTP 402 with an upgrade hint).

### Still operational policy / not implemented
- Invoice UI, proration previews, and usage-based metering → Stripe usage
  records are not wired (plans are seat/flat). Stripe handles retry/dunning
  schedules; OMERO only reacts with a notification.

Local webhook testing: `stripe listen --forward-to localhost:8000/billing/webhook`.

---

## 5. Deployment tiers & scaling

### Current (free) topology
- **Web:** Render free (512 MB) running FastAPI (`python -m omni_modal.api`).
- **DB:** Neon free Postgres + pgvector.
- **Embeddings:** `hashing` (no model load — fits 512 MB). Retrieval quality is
  the keyword-overlap baseline (recall@1 ≈ 0.40), **not** the bge semantic path.

### Upgrade path (recommended order)
1. **Web tier → Render Starter ($7) / Fly.io:** removes the 512 MB cap and the
   15-min cold-start spin-down. Then set `EMBEDDING_BACKEND=sentence-transformers`
   (bge-small, 384-dim) to get the measured recall@1 ≈ 0.875 semantic path, or
   keep the web tier light and use a **hosted embedding API**.
2. **Redis (Upstash free):** set `REDIS_URL` to activate the distributed rate
   limiter, shared query cache, durable ingestion queue, and shared refresh-token
   store — making the web tier stateless and horizontally scalable.
3. **Separate worker tier:** deploy `python -m omni_modal.ingestion.redis_worker`
   as its own service and set `INGEST_WORKER_IN_PROCESS=false` on the web service
   (see `render.yaml`). Web then only enqueues; the worker drains the durable
   queue with retries + dead-letter.
4. **Migrations in CI:** run `python scripts/migrate.py` as a release step before
   promoting new code.

### Backups & PITR
- Neon provides automated backups and point-in-time restore on paid tiers
  (history retention is plan-dependent); the free tier has limited retention.
- Before destructive migrations, take a manual branch/snapshot in Neon.
- Object storage (S3) lifecycle/versioning is configured on the bucket, not in
  app code.

---

## 6. Security posture summary

- **Auth:** short-lived access JWTs + rotating refresh tokens with reuse
  detection and server-side revocation (Redis-backed when configured). Tokens
  are in `localStorage` today; httpOnly cookies, OAuth, email verification, and
  MFA are roadmap.
- **Rate limiting:** per-tenant/per-user sliding window, distributed via Redis.
- **Tenant isolation:** app-layer filters + optional Postgres RLS (section 2).
- **Secrets:** provided via environment variables; rotate any credential that
  has been shared in plaintext. Use the platform's secret store (Render/Vercel
  env vars; a dedicated vault is better).
