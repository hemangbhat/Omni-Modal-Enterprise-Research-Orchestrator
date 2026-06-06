# Security Boundaries

## Database Credentials

`DATABASE_URL` is server-only infrastructure configuration. It must not be imported into:

- Next.js client components.
- Agent prompts or agent context.
- Orchestration decision objects.
- External delegation payloads.

The Python backend reads database credentials only through `EnvSecretStore`. The orchestrator depends on typed data access protocols, not on credential values.

## Agent Context

Agent context may include:

- Request IDs.
- Tenant IDs.
- User IDs or service principals after authorization.
- Tool names and capability references.

Agent context must not include:

- Raw database URLs.
- Neon credentials.
- Sentry DSNs when not required by the runtime.
- Model provider API keys.
- Local file paths to private model artifacts unless the local runtime explicitly needs them.

## Phase 1 Enforcement

Phase 1 includes Python unit tests that assert:

- Backend settings redact sensitive fields.
- Secret references do not stringify to raw values.
- The orchestrator health snapshot does not include database credentials.
