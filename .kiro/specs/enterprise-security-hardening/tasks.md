# Implementation Plan: Enterprise Security Hardening

## Overview

Implement ten interlocking security layers across the Python backend and Next.js 16 frontend.
Foundation modules (auth, RBAC, audit, rate limiting) are built first, then domain guards
(document access, input validation, upload safety, redactor), then everything is wired into
`main.py`, and finally the frontend middleware and upload hook are added. Unit and property-based
tests (Hypothesis) are added as sub-tasks close to each implementation step.

---

## Tasks

- [x] 1. Create the `security` package skeleton and JWT auth module
  - Create `services/api/src/omni_modal/security/__init__.py` (empty)
  - Implement `verify_jwt`, `JwtClaims`, `AuthError`, `_b64url_decode`, and
    `jwt_secret_from_env` in `services/api/src/omni_modal/security/auth.py` exactly as
    specified in design section 2.1
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.1 Write property test for JWT round-trip fidelity (Property 1)
    - **Property 1: JWT Round-Trip Fidelity**
    - **Validates: Requirements 1.3**
    - File: `services/api/tests/test_auth.py`
    - Use `@given(st.text(min_size=1), st.text(min_size=1), st.lists(st.text()), st.integers(min_value=int(time.time())+1))`
    - Encode a token with a known secret, call `verify_jwt`, assert all four claims match

  - [ ]* 1.2 Write property test for invalid JWT rejection (Property 2)
    - **Property 2: Any Invalid JWT Is Always Rejected**
    - **Validates: Requirements 1.1, 1.2, 1.5**
    - File: `services/api/tests/test_auth.py`
    - Generate a valid JWT, mutate one or more bytes in the signature segment, assert `AuthError` is raised
    - Also test absent/empty `tenant_id` claim and expired `exp`

  - [ ]* 1.3 Write unit tests for `verify_jwt` (example-based)
    - Test valid token, expired token, wrong `alg`, tampered signature, missing `tenant_id`, missing `user_id`
    - _Requirements: 1.1, 1.2, 1.5_

- [x] 2. Implement the RBAC endpoint guard
  - Implement `assert_endpoint_roles`, `RbacError`, and `ENDPOINT_ROLES` in
    `services/api/src/omni_modal/security/rbac.py` as specified in design section 2.2
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6_

  - [ ]* 2.1 Write property test for RBAC role intersection (Property 4)
    - **Property 4: RBAC Raises Error Iff Role Intersection Is Empty**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.6**
    - File: `services/api/tests/test_rbac.py`
    - Use `@given(st.frozensets(st.text()), st.sampled_from(list(ENDPOINT_ROLES.keys())))`
    - Assert `RbacError` iff `frozenset(roles) & required == ∅`

  - [ ]* 2.2 Write unit tests for `assert_endpoint_roles` (example-based)
    - Cover all three endpoints with permitted and impermissible role sets, and an unknown path
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 3. Implement the enhanced audit sink
  - Implement `AuditEntry`, `EnhancedAuditSink` protocol, `InMemoryAuditSink`, and `_scrub`
    in `services/api/src/omni_modal/security/audit.py` as specified in design section 2.7
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 9.3_

  - [ ]* 3.1 Write property test for monotonically increasing audit entry IDs (Property 13)
    - **Property 13: Audit Entry IDs Are Strictly Monotonically Increasing**
    - **Validates: Requirements 7.7**
    - File: `services/api/tests/test_audit.py`
    - Use `@given(st.integers(min_value=1, max_value=200))`
    - Call `record_tool_call` or `record_event` N times; assert IDs are `[1, 2, …, N]`

  - [ ]* 3.2 Write property test for audit scrubbing (Property 14)
    - **Property 14: Audit Scrubbing Preserves Primitives and Redacts Strings**
    - **Validates: Requirements 9.3**
    - File: `services/api/tests/test_audit.py`
    - Use `@given(st.dictionaries(st.text(), st.one_of(st.integers(), st.booleans(), st.floats(), st.text(), st.none())))`
    - Assert `_scrub(d)` preserves int/bool/float/None values and replaces all strings with `"<scrubbed>"`

  - [ ]* 3.3 Write unit tests for `InMemoryAuditSink` (example-based)
    - Test `record_tool_call` and `record_event`; verify `entries` list; verify monotonic ID;
      verify scrubbing; verify system event with `context=None`
    - _Requirements: 7.1, 7.7_

- [x] 4. Implement the sliding window rate limiter
  - Implement `RateLimitConfig`, `RateLimitExceeded`, and `SlidingWindowRateLimiter` with
    `check_tenant`, `check_user`, `check_delegation` in
    `services/api/src/omni_modal/security/rate_limiting.py` as specified in design section 2.8
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 4.1 Write property test for rate limiter window boundary (Property 15)
    - **Property 15: Rate Limiter Allows Exactly `limit` Requests Per Window**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.6**
    - File: `services/api/tests/test_rate_limiter.py`
    - Use `@given(st.integers(min_value=1, max_value=30), st.integers(min_value=1, max_value=10))`
    - Configure a limiter with limit `L`; fire `L + k` requests; assert first `L` pass and remaining `k` raise `RateLimitExceeded`

  - [ ]* 4.2 Write unit tests for `SlidingWindowRateLimiter` (example-based)
    - Test burst exactly at limit, burst exceeding limit, window expiry with mock time,
      `Retry-After` value, and all three bucket scopes (tenant, user, delegation)
    - _Requirements: 8.1, 8.2, 8.3, 8.6_

- [x] 5. Checkpoint — foundation modules complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement the document access guard
  - Implement `AccessMetadata`, `Visibility`, `AccessDenied`, `check_access`, and
    `DocumentAccessGuard` in `services/api/src/omni_modal/security/document_access.py`
    as specified in design section 2.3
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 6.1 Write property test for access guard membership correctness (Property 5)
    - **Property 5: Document Access Guard Returns Only Permitted Documents**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
    - File: `services/api/tests/test_document_access.py`
    - Use `@given(st.lists(st.builds(DocumentSummary, ...)), st.builds(ToolContext, ...))`
    - Assert guard result equals the sublist where `check_access` returns `True`

  - [ ]* 6.2 Write property test for cross-tenant isolation (Property 6)
    - **Property 6: Cross-Tenant Access Always Denied**
    - **Validates: Requirements 3.6**
    - File: `services/api/tests/test_document_access.py`
    - Use `@given(st.text(min_size=1), st.text(min_size=1))` with `assume(a != b)`
    - Assert `check_access` returns `False` for all visibility modes when tenant IDs differ

  - [ ]* 6.3 Write property test for private visibility owner match (Property 7)
    - **Property 7: Private Visibility Grants Access Iff Owner Matches**
    - **Validates: Requirements 3.1**
    - File: `services/api/tests/test_document_access.py`
    - Use `@given(st.text(min_size=1), st.text(min_size=1))` for `user_id` and `owner_id`
    - Assert `check_access` returns `True` iff `actor_user_id == owner_id`

  - [ ]* 6.4 Write unit tests for `check_access` and `DocumentAccessGuard` (example-based)
    - Cover all three visibility values, cross-tenant, restricted with allowed/denied users and roles
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 7. Implement the input validator
  - Implement `ValidationError`, `assert_body_size`, `assert_query_length`,
    `assert_tenant_id`, and `assert_document_id_uuid` in
    `services/api/src/omni_modal/security/input_validation.py` as specified in design section 2.4
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7_

  - [ ]* 7.1 Write property test for query length boundary (Property 8)
    - **Property 8: Query Length Validation Boundary**
    - **Validates: Requirements 4.3**
    - File: `services/api/tests/test_input_validation.py`
    - Use `@given(st.text(min_size=0, max_size=MAX_QUERY_CHARS * 2))`
    - Assert `ValidationError` iff `len(s) > MAX_QUERY_CHARS`

  - [ ]* 7.2 Write property test for UUID v4 validation (Property 9)
    - **Property 9: UUID v4 Validation Accepts Valid, Rejects Invalid**
    - **Validates: Requirements 4.5**
    - File: `services/api/tests/test_input_validation.py`
    - Use `@given(st.uuids(version=4))` for valid and `@given(st.text())` for invalid
    - Assert valid UUIDs pass unchanged; non-matching strings raise `ValidationError`

  - [ ]* 7.3 Write property test for tenant ID validation (Property 10)
    - **Property 10: Tenant ID Validation Enforces Length and Type**
    - **Validates: Requirements 4.4**
    - File: `services/api/tests/test_input_validation.py`
    - Use `@given(st.one_of(st.text(min_size=0, max_size=256), st.integers(), st.none()))`
    - Assert accepts non-empty strings ≤ 128 chars; rejects all other inputs with `ValidationError`

  - [ ]* 7.4 Write unit tests for input validators (example-based)
    - Boundary values for each function; test body size at exactly 1 MiB and 1 MiB + 1 byte
    - _Requirements: 4.1, 4.3, 4.4, 4.5_

- [x] 8. Implement the upload safety guard
  - Implement `UploadSafetyError`, `sniff_mime_type`, and `assert_upload_safe` in
    `services/api/src/omni_modal/security/upload_safety.py` as specified in design section 2.5;
    add the `ALLOWED_MIME_TYPES` and `EXTENSION_TO_MIME` constants
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 8.1 Write property test for upload safety size and MIME rejection (Property 16)
    - **Property 16: Upload Safety Rejects Oversized or Disallowed MIME Files**
    - **Validates: Requirements 5.1, 5.2**
    - File: `services/api/tests/test_upload_safety.py`
    - Use `@given(st.integers(min_value=0, max_value=MAX_FILE_BYTES * 2))` for size and
      `st.sampled_from(...)` for MIME strings; mock `file_path.stat()` and `sniff_mime_type`
    - Assert `UploadSafetyError` when size exceeds limit or MIME is not in `ALLOWED_MIME_TYPES`

  - [ ]* 8.2 Write unit tests for `assert_upload_safe` (example-based)
    - Test file at 50 MiB, 50 MiB + 1 byte; test each allowed extension; test MIME mismatch;
      test that `file_size_bytes` and `detected_mime` are returned on success
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 9. Implement the content redactor
  - Implement `ContentLeakError`, `_fingerprint`, `_redact_chunk_content`, and
    `redact_request` in `services/api/src/omni_modal/security/redactor.py` as specified
    in design section 2.6
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 9.1 Write property test for redactor no-chunk-leak invariant (Property 11)
    - **Property 11: Redactor Never Leaks Chunk Content in Internal Status**
    - **Validates: Requirements 6.1, 6.4**
    - File: `services/api/tests/test_redactor.py`
    - Use `@given(st.lists(st.text(min_size=16)), st.builds(A2AResearchRequest, ...))`
    - Assert no verbatim substring ≥ 16 chars from any chunk text appears in `result.internal_status`

  - [ ]* 9.2 Write property test for redactor truncation invariant (Property 12)
    - **Property 12: Redactor Always Truncates Internal Status to Maximum Length**
    - **Validates: Requirements 6.4**
    - File: `services/api/tests/test_redactor.py`
    - Use `@given(st.text(min_size=0, max_size=2000))` for `internal_status`
    - Assert `len(result.internal_status) <= MAX_INTERNAL_STATUS_CHARS` for all inputs

  - [ ]* 9.3 Write unit tests for `redact_request` (example-based)
    - Test truncation at exactly 500 chars and 501 chars; fingerprint replacement; `ContentLeakError`
      when question contains chunk content prefix
    - _Requirements: 6.1, 6.2, 6.4, 6.5_

- [x] 10. Checkpoint — domain guard modules complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Wire security middleware into `main.py`
  - [x] 11.1 Add `_authenticate` helper to `OmniModalHandler`
    - Import `verify_jwt`, `jwt_secret_from_env`, and `AuthError` from `security/auth`
    - Implement `_authenticate(path)` that returns `JwtClaims` or writes HTTP 401 and returns `None`
    - Skip auth only when `self.path == "/health"`; write an audit event via `InMemoryAuditSink`
      for every auth failure
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 11.2 Add RBAC check to protected endpoint handlers
    - Import `assert_endpoint_roles` and `RbacError` from `security/rbac`
    - Call `assert_endpoint_roles` after authentication in `do_POST`; on `RbacError` write HTTP 403
      and record an audit `access_denied` event
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6_

  - [x] 11.3 Add rate limiter to request entry point
    - Instantiate `SlidingWindowRateLimiter` as a class-level attribute on `OmniModalHandler`
    - Call `check_tenant` and `check_user` at the top of `do_GET` and `do_POST`, before body
      parsing; on `RateLimitExceeded` write HTTP 429 with `Retry-After` header and record
      a `rate_limited` audit event
    - _Requirements: 8.1, 8.2, 8.4, 8.5_

  - [x] 11.4 Add input validation to `do_POST` handlers
    - Call `assert_body_size` before reading the body in `_read_json_body`; call
      `assert_query_length` and `assert_tenant_id` in `_handle_query`; call
      `assert_document_id_uuid` in the ingestion handler; raise HTTP 413 for body size
      and HTTP 400 for all other `ValidationError` cases
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7_

  - [x] 11.5 Integrate upload safety into the ingestion flow
    - Call `assert_upload_safe` at the start of `MultimodalIngestionPipeline.ingest()` before
      extraction; on `UploadSafetyError` return a failed `IngestionResult` with
      `UNSUPPORTED_SOURCE`; record `file_size_bytes` and `detected_mime_type` as breadcrumbs
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 11.6 Integrate the redactor into the A2A delegation path
    - Import `redact_request` and `ContentLeakError` from `security/redactor`
    - Call `redact_request` in `InternalResearchAdkWorkflow.answer` before `HttpA2AResearchClient.delegate`;
      on `ContentLeakError` record a redaction audit event and return `status: "redacted"` without delegating;
      call `rate_limiter.check_delegation` before delegation
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 8.3_

  - [x] 11.7 Integrate `DocumentAccessGuard` into `McpToolRouter`
    - Wrap the `McpDataAccess` instance with `DocumentAccessGuard` before passing it to
      `McpToolRouter`; record cross-tenant access attempts via `InMemoryAuditSink`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 11.8 Expand audit coverage for query and ingestion completions
    - After `_handle_query` completes (success or failure), record an audit entry carrying
      `actor_user_id`, `tenant_id`, truncated query hash, and status
    - After ingestion completes, record an entry carrying `actor_user_id`, `document_id`,
      `tenant_id`, `status`, and `error_code`
    - _Requirements: 7.2, 7.3_

- [x] 12. Checkpoint — backend integration complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Implement the Next.js 16 frontend security middleware
  - Create `apps/web/src/middleware.ts` implementing `middleware()` as specified in design
    section 2.9 — redirect unauthenticated users to `/sign-in`, return HTTP 403 for
    insufficient roles on admin-only paths; roles must come from `getServerSession`, never
    from cookies or URL parameters
  - _Requirements: 10.1, 10.2, 10.3_

  - [ ]* 13.1 Write unit tests for the Next.js middleware (TypeScript)
    - Mock `NextRequest` / `NextResponse` and `getServerSession`; test unauthenticated redirect,
      researcher role on admin path returns 403, admin role on admin path passes through
    - _Requirements: 10.1, 10.2, 10.3_

- [x] 14. Implement the frontend upload validation hook
  - Create `apps/web/src/hooks/useUploadValidation.ts` exporting `validateUploadFile` as
    specified in design section 2.9; use `ALLOWED_EXTENSIONS` set and `MAX_FILE_BYTES` constant
  - _Requirements: 10.5_

  - [ ]* 14.1 Write property test for frontend upload validation (Property 18)
    - **Property 18: Frontend Upload Validation Rejects Disallowed Extensions and Oversized Files**
    - **Validates: Requirements 10.5**
    - File: `services/api/tests/test_auth.py` is Python-only; write a TypeScript test
      at `apps/web/src/hooks/useUploadValidation.test.ts`
    - Use fast-check or a loop over generated inputs: random extensions and sizes;
      assert `null` for allowed extension + size ≤ limit, non-null otherwise

  - [ ]* 14.2 Write unit tests for `validateUploadFile` (example-based)
    - Test each allowed extension at exactly 50 MiB and 50 MiB + 1 byte; test each disallowed
      extension; test extension case-sensitivity
    - _Requirements: 10.5_

- [x] 15. Add `SecretRef` no-leak property test
  - [ ]* 15.1 Write property test for `SecretRef` string representation (Property 17)
    - **Property 17: SecretRef String Representation Never Reveals Secret Value**
    - **Validates: Requirements 9.5**
    - File: `services/api/tests/test_auth.py` (or a dedicated `test_secrets.py`)
    - Use `@given(st.text(min_size=1), st.text(min_size=1))` for name and value (monkeypatched env)
    - Assert neither `str(SecretRef(name=n))` nor `repr(SecretRef(name=n))` contains the raw value `v`

- [x] 16. Add property test for unauthenticated non-health paths (Property 3)
  - [ ]* 16.1 Write property test for HTTP 401 on any non-health path without token
    - **Property 3: Unauthenticated Requests to Any Non-Health Path Are Rejected**
    - **Validates: Requirements 1.4**
    - File: `services/api/tests/test_auth.py`
    - Use `@given(st.text(min_size=1).filter(lambda p: p != "/health"))`
    - Construct a minimal `OmniModalHandler` mock; assert any request to a non-health path
      without a bearer token receives HTTP 401 and the handler body does not execute

- [x] 17. Final checkpoint — full test suite passes
  - Run `python -m unittest discover -s services/api/tests`
  - Run `npm run typecheck:web`
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP delivery.
- Each task references specific requirements for full traceability.
- Foundation modules (tasks 1–4) are designed to be independent and can be implemented in
  parallel across the four files.
- Property-based tests (Properties 1–18) use `hypothesis` with `@settings(max_examples=100)`
  and are placed immediately after the implementation they validate to catch regressions early.
- The `InMemoryAuditSink` is used throughout; a persistent sink backed by the `audit_logs` table
  can be swapped in via constructor injection without changing any other module.
- `sniff_mime_type` degrades gracefully if neither `python-magic` nor `filetype` is installed —
  the MIME check is skipped, not errored.
- The Next.js middleware relies on a `getServerSession` helper at `@/lib/session` which must be
  implemented (or stubbed) as part of task 13.
- Property 18 (frontend validation) is best tested in TypeScript; the Python test runner does not
  cover TypeScript files. Use `npm run typecheck:web` for the TypeScript surface.

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "2.1", "2.2", "3.1", "3.2", "3.3", "4.1", "4.2"] },
    { "id": 1, "tasks": ["6.1", "6.2", "6.3", "6.4", "7.1", "7.2", "7.3", "7.4", "8.1", "8.2", "9.1", "9.2", "9.3"] },
    { "id": 2, "tasks": ["11.1", "11.2", "11.3", "11.4", "11.5", "11.6", "11.7", "11.8"] },
    { "id": 3, "tasks": ["13.1", "14.1", "14.2", "15.1", "16.1"] }
  ]
}
```
