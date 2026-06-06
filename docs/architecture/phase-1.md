# Phase 1 Architecture

## Scope

Phase 1 builds the minimum clean foundation:

- Next.js App Router web shell with a local health endpoint.
- Python backend package with orchestration contracts.
- Drizzle schema package and pgvector migration draft.
- Environment contract for frontend, backend, and infrastructure.
- Security documentation and tests for credential boundaries.

## Explicitly Deferred

The following systems are represented as interfaces or configuration placeholders only:

- Local Whisper transcription.
- QLoRA entity extraction.
- MCP-backed internal data access.
- ADK orchestration.
- A2A or Gemini Interactions API delegation.
- Sentry runtime instrumentation.

They are not implemented in Phase 1 because real integration details, credentials, or model artifacts are required.

## Known Dependency Risk

As of this scaffold, Next.js `16.2.7` installs an internal `postcss@8.4.31`, and `npm audit` reports advisory `GHSA-qx2v-qp2m-jg93` against that nested dependency. The app's direct PostCSS dependency is pinned above the fixed version, and npm's suggested fix downgrades Next to `9.3.3`, which is not compatible with this App Router project. This remains a tracked upstream framework dependency risk for Phase 1.

## Boundary Model

```text
Web App -> Backend API -> Orchestrator -> Internal Data Access Interface -> DB Infrastructure
                                      -> ML Interfaces
                                      -> External Delegation Interface
```

The orchestrator receives capabilities, not secrets. It can ask an internal data access service for records, but it cannot inspect raw connection strings.
