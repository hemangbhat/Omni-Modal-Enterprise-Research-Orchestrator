# Omni-Modal Enterprise Research Orchestrator

Phase 1 is the production foundation for the system. It sets up the web app, database package, Python orchestration package, security boundaries, and validation checks without wiring unfinished ML or delegation integrations.

## Structure

- `apps/web`: Next.js App Router, TypeScript, and Tailwind CSS shell.
- `packages/db`: Drizzle schema and initial pgvector migration SQL.
- `services/api`: Python orchestration backend contracts and health snapshot.
- `docs`: Phase and security notes.

## Run

Install JavaScript dependencies:

```bash
npm install
```

Run the web app:

```bash
npm run dev:web
```

Run backend validation tests:

```bash
npm run test:backend
```

Run all Phase 1 validation:

```bash
npm run validate
```

Apply the Phase 1 database migration manually or through your deployment migration runner using `packages/db/drizzle/0001_initial.sql`. The `drizzle-kit` CLI is intentionally not included in Phase 1 because its current npm release carries a dev-server audit advisory.

## Security Boundary

The agent orchestration layer must never receive raw database credentials. Database access is represented through typed internal data access interfaces. Only infrastructure code may read `DATABASE_URL`, and Phase 1 tests enforce that orchestrator contracts do not expose credential-bearing fields.
