# Requirements Document

## Introduction

Phase 10 hardens the Omni-Modal Enterprise Research Orchestrator for enterprise production use.
The system handles confidential research documents for multiple tenants and delegates some queries
to external AI agents (A2A / Gemini Interactions API). This phase adds authentication,
role-based access control, document-level access enforcement, input validation, upload safety
checks, pre-delegation content redaction, audit logging completeness, and rate limiting.

The goal is to enforce clear security boundaries in both the Python HTTP backend
(`services/api/src/omni_modal/`) and the Next.js 16 frontend (`apps/web/`), minimise data
exposure to external services, and make all security-relevant actions auditable.

---

## Glossary

- **API_Server**: The Python HTTP service at `services/api/src/omni_modal/main.py`.
- **Auth_Middleware**: The backend component that validates JWT bearer tokens on every protected
  HTTP endpoint before the request reaches any handler.
- **JWT**: A signed JSON Web Token used to authenticate callers to the API_Server.
- **ToolContext**: The dataclass (`mcp/models.py`) carrying `tenant_id`, `actor_user_id`, and
  `roles` that is threaded through every MCP tool invocation.
- **ToolPermissionGuard**: The existing class (`mcp/permissions.py`) that raises
  `PermissionDeniedError` when the caller's roles do not satisfy a tool's requirements.
- **AccessMetadata**: The JSON column on `documents` and `users` tables carrying `visibility`
  (`private` | `tenant` | `restricted`), `allowedUserIds`, `allowedRoles`, and `sensitivity`.
- **Document_Access_Guard**: The new backend component that enforces `AccessMetadata` rules
  before returning any document, chunk, or entity record to the caller.
- **Redactor**: The backend component responsible for stripping internal document content from
  any payload before it is forwarded to an external service.
- **A2A_Client**: The `HttpA2AResearchClient` in `orchestration/a2a.py` that delegates research
  queries to the external Gemini Interactions endpoint.
- **Audit_Sink**: The `AuditSink` protocol (`mcp/data_access.py`) whose `record_tool_call`
  implementation persists entries to the `audit_logs` table.
- **Rate_Limiter**: The backend component that tracks request counts per tenant and per user
  within a sliding time window.
- **Ingestion_Pipeline**: The `MultimodalIngestionPipeline` in `ingestion/pipeline.py` that
  processes PDF and audio uploads.
- **SecretRef**: The `SecretRef` dataclass in `security/secrets.py` whose `__str__` and
  `__repr__` never expose the underlying secret value.
- **Tenant**: An isolated organisational unit; all data rows carry a `tenant_id` foreign key.
- **Researcher**: A tenant user with the `researcher` role; can read and search documents.
- **Admin**: A tenant user with the `admin` role; can manage users and read audit logs.
- **Auditor**: A tenant user with the `auditor` role; can read audit logs but not modify data.
- **Unauthenticated_Caller**: Any HTTP client that presents no bearer token or an invalid one.
- **Cross_Tenant_Request**: A request where the authenticated user's `tenant_id` differs from
  the `tenant_id` of the requested resource.

---

## Requirements

### Requirement 1: Authentication

**User Story:** As an enterprise operator, I want every API endpoint to require a valid bearer
token, so that unauthenticated callers cannot read or write any tenant data.

#### Acceptance Criteria

1. WHEN a request arrives at any protected endpoint without a bearer token, THE Auth_Middleware
   SHALL reject the request with HTTP 401.
2. WHEN a request arrives with a bearer token whose signature is invalid or whose expiry has
   passed, THE Auth_Middleware SHALL reject the request with HTTP 401.
3. WHEN a request carries a valid, unexpired bearer token, THE Auth_Middleware SHALL extract
   `tenant_id`, `user_id`, and `roles` claims and make them available to the request handler.
4. THE API_Server SHALL expose `/health` as the only endpoint that does not require
   authentication; all requests to any other endpoint without a valid bearer token SHALL be
   rejected with HTTP 401.
5. IF the bearer token contains no `tenant_id` claim, THEN THE Auth_Middleware SHALL reject the
   request with HTTP 401 and log the rejection reason to the Audit_Sink.

---

### Requirement 2: Role-Based Access Control on HTTP Endpoints

**User Story:** As an enterprise operator, I want each HTTP endpoint to enforce role
requirements, so that users can only perform actions permitted by their role.

#### Acceptance Criteria

1. WHEN a user with the `researcher` role submits a request to `/query` or `/query/stream`, THE
   API_Server SHALL process the request.
2. WHEN a user with no role or an unrecognised role submits a request to `/query` or
   `/query/stream`, THE API_Server SHALL return HTTP 403.
3. WHEN a user with the `researcher` or `admin` role submits a request to `/ingest/local`, THE
   API_Server SHALL process the request.
4. WHEN a user without `researcher` or `admin` role submits a request to `/ingest/local`, THE
   API_Server SHALL return HTTP 403 and record a denied-access audit entry via the Audit_Sink.
5. WHEN a user with the `admin` or `auditor` role requests the `get_audit_logs` MCP tool, THE
   ToolPermissionGuard SHALL allow the call, consistent with the existing `TOOL_PERMISSIONS`
   configuration.
6. FOR ALL protected endpoints, WHEN a role check fails, THE API_Server SHALL return HTTP 403
   and SHALL record a denied-access audit entry via the Audit_Sink before returning the
   response.

---

### Requirement 3: Document-Level Access Control

**User Story:** As an enterprise operator, I want document visibility rules to be enforced at
query time, so that users cannot read documents outside their permitted scope.

#### Acceptance Criteria

1. WHEN a user requests a document whose `AccessMetadata.visibility` is `private`, THE
   Document_Access_Guard SHALL permit access only if the requesting user's `user_id` matches the
   document's `owner_id`.
2. WHEN a user requests a document whose `AccessMetadata.visibility` is `tenant`, THE
   Document_Access_Guard SHALL permit access to any authenticated user within the same tenant.
3. WHEN a user requests a document whose `AccessMetadata.visibility` is `restricted`, THE
   Document_Access_Guard SHALL permit access only if the requesting user's `user_id` appears in
   `AccessMetadata.allowedUserIds` or the user's role appears in `AccessMetadata.allowedRoles`.
4. IF a user requests a document and the access check fails, THEN THE Document_Access_Guard
   SHALL return an empty result rather than an error that confirms the document's existence.
5. THE Document_Access_Guard SHALL enforce access rules on all document request types: the
   `get_document`, `search_documents`, `search_chunks`, and `get_entities` MCP tools, and any
   HTTP endpoint that returns document data.
6. WHEN a user submits a Cross_Tenant_Request, THE Document_Access_Guard SHALL return an empty
   result and record a cross-tenant access attempt in the Audit_Sink.

---

### Requirement 4: Input Validation

**User Story:** As an enterprise operator, I want all HTTP request inputs to be validated before
processing, so that malformed or oversized inputs cannot corrupt data or cause unexpected errors.

#### Acceptance Criteria

1. WHEN a request body exceeds 1 048 576 bytes (1 MiB), THE API_Server SHALL reject it and
   return HTTP 413 with a JSON error body containing a human-readable `error` field.
2. WHEN a request body is not valid UTF-8 JSON or the top-level value is not a JSON object, THE
   API_Server SHALL return HTTP 400.
3. WHEN the `/query` or `/query/stream` endpoint receives a `query` field whose length exceeds
   4 096 characters, THE API_Server SHALL return HTTP 400.
4. WHEN the `/query` endpoint receives a `tenant_id` field that is not a non-empty string of at
   most 128 characters, THE API_Server SHALL return HTTP 400.
5. WHEN the `/ingest/local` endpoint receives a `document_id` that is not a valid UUID v4
   string, THE API_Server SHALL return HTTP 400.
6. WHEN an MCP tool receives an argument that does not conform to the tool's `input_schema`, THE
   ToolPermissionGuard SHALL reject the call with a `ToolValidationError` before invoking the
   handler.
7. IF any input validation check fails, THEN THE API_Server SHALL return a JSON error body
   containing a human-readable `error` field and SHALL NOT return HTTP 500 for validation
   failures.

---

### Requirement 5: Upload Safety Checks

**User Story:** As an enterprise operator, I want uploaded files to be checked before ingestion,
so that oversized, unsupported, or potentially dangerous files are rejected before processing.

#### Acceptance Criteria

1. WHEN an upload request is received, THE Ingestion_Pipeline SHALL reject any file whose
   declared byte size exceeds 52 428 800 bytes (50 MiB) with an `UNSUPPORTED_SOURCE` error code.
2. WHEN an upload request is received, THE Ingestion_Pipeline SHALL reject any file whose
   content-sniffed MIME type does not match the allowed set (`application/pdf`, `audio/mpeg`,
   `audio/wav`, `audio/mp4`, `audio/flac`, `audio/ogg`, `audio/webm`) with an
   `UNSUPPORTED_SOURCE` error code.
3. WHEN an upload request is received for a file with a supported extension but mismatched MIME
   type, THE Ingestion_Pipeline SHALL reject the file with an `UNSUPPORTED_SOURCE` error code
   and record the mismatch in the ingestion breadcrumb.
4. THE Ingestion_Pipeline SHALL record `file_size_bytes` and `detected_mime_type` in the
   observability breadcrumb for every upload attempt, whether accepted or rejected.
5. IF a file passes extension and MIME checks but extraction produces no text, THEN THE
   Ingestion_Pipeline SHALL return an `EMPTY_TEXT` error, consistent with existing behaviour.

---

### Requirement 6: Sensitive Content Redaction Before External Delegation

**User Story:** As an enterprise operator, I want internal document content to be stripped from
any payload sent to an external AI agent, so that confidential data does not leave the tenant's
security boundary.

#### Acceptance Criteria

1. THE Redactor SHALL strip all document chunk text, entity values, and source URIs from the
   payload before the A2A_Client sends a delegation request.
2. WHEN building an `A2AResearchRequest`, THE Redactor SHALL populate the `question` field with
   only the user's original question and a brief, non-content context summary; raw chunk content
   SHALL NOT appear in the `question` field.
3. THE A2A_Client SHALL set `contains_internal_content: false` in the delegation message
   metadata and SHALL NOT include any field whose value is derived from a document chunk.
4. WHEN an `internal_status` summary is included in the delegation payload, THE Redactor SHALL
   limit it to a maximum of 500 characters and SHALL replace any text that matches a chunk
   content fingerprint with the placeholder `[REDACTED]`.
5. IF a redaction check detects internal content in a candidate delegation payload, THEN THE
   Redactor SHALL abort the delegation, return a `status: "redacted"` response to the caller,
   and record a redaction event in the Audit_Sink.

---

### Requirement 7: Audit Logging Completeness

**User Story:** As an enterprise auditor, I want all security-relevant actions to be logged with
actor, resource, and outcome, so that any suspicious activity can be investigated.

#### Acceptance Criteria

1. THE Audit_Sink SHALL record an entry for every MCP tool call, carrying `actor_user_id`,
   `tool_name`, sanitised arguments, `status` (`ok` | `denied` | `error`), and a
   monotonic timestamp.
2. WHEN an ingestion request completes (success or failure), THE Audit_Sink SHALL record an
   entry carrying `actor_user_id`, `document_id`, `tenant_id`, `status`, and `error_code` if
   applicable.
3. WHEN a `/query` or `/query/stream` request completes, THE Audit_Sink SHALL record an entry
   carrying `actor_user_id`, `tenant_id`, a truncated query hash (not the raw query text), and
   `status`.
4. WHEN an authentication or authorisation failure occurs, THE Audit_Sink SHALL record an entry
   carrying the request path, the failure reason, and the client IP address.
5. WHEN an external delegation is attempted, THE Audit_Sink SHALL record an entry carrying
   `request_id`, `tenant_id`, `user_id`, delegation `status`, and whether redaction was applied.
6. THE Audit_Sink SHALL persist audit entries atomically; IF the persistence operation fails,
   THEN THE API_Server SHALL log the failure to the observability system and continue serving
   the original request rather than returning an error to the caller.
7. WHILE the system is running, THE Audit_Sink SHALL assign each entry a unique, monotonically
   increasing `id` within a tenant scope so that audit log queries can be paginated reliably.
   Uniqueness of entry IDs is only enforced while the system is running.

---

### Requirement 8: Rate Limiting

**User Story:** As an enterprise operator, I want per-tenant and per-user request rate limits,
so that a single tenant or user cannot exhaust shared compute resources or trigger excessive
external delegation costs.

#### Acceptance Criteria

1. WHEN a tenant exceeds 60 requests per minute across all endpoints, THE Rate_Limiter SHALL
   return HTTP 429 with a `Retry-After` header specifying the number of seconds until the
   window resets.
2. WHEN a single user exceeds 20 requests per minute, THE Rate_Limiter SHALL return HTTP 429
   with a `Retry-After` header immediately upon the 21st request, with no grace period.
3. WHEN a tenant exceeds 10 external delegation requests per hour, THE Rate_Limiter SHALL block
   further delegation calls and return a `status: "rate_limited"` response to the caller.
4. THE Rate_Limiter SHALL apply limits before request body parsing so that malformed or
   oversized bodies do not consume rate-limit budget before the limit check.
5. IF a request is rejected by the Rate_Limiter, THEN THE Audit_Sink SHALL record a
   rate-limit event carrying `tenant_id`, `user_id`, `endpoint`, and the current request count.
6. THE Rate_Limiter SHALL use a sliding window algorithm so that burst traffic at window
   boundaries does not grant double the permitted capacity.

---

### Requirement 9: Credential and Secret Boundary Enforcement

**User Story:** As an enterprise operator, I want secrets and credentials to never appear in
agent context, delegation payloads, audit logs, or API responses, so that credential leakage is
impossible by design.

#### Acceptance Criteria

1. THE API_Server SHALL ensure that no HTTP response body contains a raw value corresponding to
   any `SecretRef`-named environment variable (e.g., `DATABASE_URL`, `SENTRY_DSN`,
   `GEMINI_API_KEY`).
2. THE A2A_Client SHALL ensure that no field in a delegation request message contains a raw
   database URL, model provider API key, or Sentry DSN.
3. WHEN the Audit_Sink serialises an audit entry, THE Audit_Sink SHALL scrub all argument
   values before writing to the `audit_logs` table, replacing any value that is not a bare
   integer, boolean, or null with a `<scrubbed>` placeholder.
4. THE ToolContext serialised for agent context SHALL contain only `tenant_id`, `actor_user_id`,
   `roles`, and `request_id`; it SHALL NOT contain any credential field.
5. IF a `SecretRef` value is accidentally passed to a string-serialisation path, THEN THE
   `SecretRef.__str__` method SHALL return the redacted representation
   `SecretRef(name=..., value=<redacted>)` rather than the raw secret, consistent with the
   existing implementation in `security/secrets.py`.

---

### Requirement 10: Frontend Access Enforcement

**User Story:** As an enterprise operator, I want the Next.js frontend to enforce the same
access boundaries as the backend, so that restricted pages and actions are not reachable through
the UI even before an API call is made.

#### Acceptance Criteria

1. WHEN an unauthenticated user navigates to any page under `/documents`, `/research`, or
   `/upload`, THE Web_App SHALL redirect the user to the sign-in page.
2. WHEN an authenticated user with the `researcher` role attempts to access an admin-only page,
   THE Web_App SHALL render an HTTP 403 response and display an access-denied message.
3. THE Web_App SHALL read the user's roles exclusively from the server-side session and SHALL
   NOT trust role values supplied in client-side cookies or URL parameters.
4. WHEN a document's `AccessMetadata.visibility` is `private` or `restricted`, THE Web_App
   SHALL omit that document from list views for users who fail the access check, rather than
   showing a locked placeholder that reveals the document's existence.
5. WHEN the frontend submits a file for upload, THE Web_App SHALL validate that the file
   extension matches an allowed type (`.pdf`, `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.webm`)
   and that the file size does not exceed 50 MiB before dispatching the upload request.
