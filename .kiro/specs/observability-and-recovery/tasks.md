# Implementation Plan: Observability and Recovery

## Overview

Implement full-stack Sentry observability and intelligent recovery across the Omni-Modal Enterprise Research Orchestrator. The implementation follows a layered dependency order:

1. **Foundation** — enhance `observability.py` (PII scrubbing, `continue_trace`, `child_span`, `set_request_scope`) and `retry.py` (jitter, `max_total_delay`, Retry-After, retryable classifier)
2. **Fallback layer** — create `orchestration/fallbacks.py` (`FallbackController`, `OrchestrationResult`)
3. **Subsystem instrumentation** — ingestion pipeline, transcription, retrieval, MCP tool calls, orchestration timeouts, external delegation
4. **Frontend** — Sentry SDK init, error boundary, instrumented fetch wrapper (`api-client.ts`)
5. **Tests** — property-based and unit tests for all correctness properties

---

## Tasks

- [x] 1. Enhance the Observability facade with distributed tracing and PII scrubbing

  Extend `services/api/src/omni_modal/observability.py` with:
  - `continue_trace(headers: dict[str, str]) -> ContextManager` — reads `sentry-trace` and `baggage` headers and starts/continues a Sentry transaction; if headers are absent or malformed, starts a new root transaction without raising
  - `child_span(operation: str, description: str) -> ContextManager` — wraps `sentry_sdk.start_span` nested under the active transaction; no-ops when SDK is unavailable
  - `set_request_scope(tenant_id: str, user_id: str | None = None) -> None` — sets `tenant_id` and (optionally) `user_id` as Sentry scope tags on the current request
  - `scrub_pii(data: dict[str, Any]) -> dict[str, Any]` — iterates over all string values in a dict and applies `scrub_value()` regex replacements (email, URL/connection-string, password/secret/token key-value) on every string value; returns a new dict with redacted values
  - Standalone `scrub_value(value: str) -> str` helper (PII pattern list: email, URL, secret key-value)
  - Wire `scrub_pii` into the existing `before_send` hook so all outbound Sentry payloads are scrubbed
  - _Requirements: 2.3, 2.5, 12.2, 12.3_

  - [ ]* 1.1 Write property test for PII scrubbing completeness
    - **Property 1: PII Scrubbing Completeness**
    - Generate strings with embedded emails, connection strings (e.g. `postgres://...`), and `password=secret` key-value pairs using `hypothesis`; assert `scrub_value()` output contains no original PII tokens and all non-PII text is preserved
    - Test file: `services/api/tests/test_pii_scrubbing.py`
    - **Validates: Requirements 2.5, 6.5**

  - [ ]* 1.2 Write unit tests for Observability facade
    - Sentry init with valid DSN (smoke — verify `_sentry` is set)
    - Sentry init with missing DSN (no error, `_sentry` stays `None`)
    - `continue_trace` with valid `sentry-trace` header produces a linked transaction
    - `continue_trace` with malformed/missing headers starts a new root transaction without raising
    - `set_request_scope` with SDK unavailable does not raise
    - Test file: `services/api/tests/test_observability.py`
    - _Requirements: 2.1, 2.2, 12.2, 12.3_

- [x] 2. Enhance the retry decorator with jitter, max-total-delay, Retry-After, and retryable classifier

  Modify `services/api/src/omni_modal/retry.py`:
  - Add `max_total_delay: float = 30.0` (seconds) parameter; once the cumulative elapsed sleep time would exceed this ceiling, treat the exception as exhausted even if `max_retries` has not been reached
  - Add `jitter_factor: float = 0.25` parameter; compute delay as `base_delay * 2^attempt + random.uniform(0, jitter_factor * base_delay * 2^attempt)`
  - Add `respect_retry_after: bool = True` parameter; when an HTTP 429 response exposes a `Retry-After` header with a numeric value ≤ `max_total_delay`, use that value as the next delay instead of the exponential formula
  - Add `is_retryable(exc: BaseException) -> bool` function:
    - Retryable: `ConnectionError`, `TimeoutError`, HTTP 429/502/503/504, database connection timeout
    - Non-retryable: HTTP 400, 401, 403, `ValidationError`, `FileFormatError`
  - On successful retry after failures, record a recovery breadcrumb via `observability.add_breadcrumb` with total attempts used
  - Breadcrumb on each retry: include attempt number, delay in seconds, and exception type name
  - On exhaustion: `capture_exception` with `total_attempts` and `cumulative_elapsed_ms` in context
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_

  - [ ]* 2.1 Write property test for exponential backoff with jitter bounds
    - **Property 2: Exponential Backoff with Jitter Bounds**
    - Generate `(base_delay, attempt, jitter_factor)` tuples with `hypothesis`; assert `base_delay * 2^attempt ≤ actual_delay ≤ base_delay * 2^attempt * (1 + jitter_factor)`
    - Test file: `services/api/tests/test_retry.py`
    - **Validates: Requirements 9.1, 9.7**

  - [ ]* 2.2 Write property test for retryable exception classification
    - **Property 3: Retryable Exception Classification Correctness**
    - Generate exception instances from both retryable and non-retryable sets; assert `is_retryable()` returns the correct boolean for every generated exception
    - Test file: `services/api/tests/test_retry.py`
    - **Validates: Requirements 9.4, 9.5**

  - [ ]* 2.3 Write property test for Retry-After header override
    - **Property 4: Retry-After Header Override**
    - Generate numeric `Retry-After` values (within and exceeding `max_total_delay`); assert decorator uses the header value when it is within bounds and falls back to exponential backoff when it exceeds the ceiling
    - Test file: `services/api/tests/test_retry.py`
    - **Validates: Requirements 9.8**

  - [ ]* 2.4 Write property test for retry breadcrumb count
    - **Property 5: Retry Breadcrumb Count Matches Attempt Count**
    - Generate `max_retries` values N; simulate a function that always fails; assert exactly N breadcrumbs were recorded and the final exception context contains `attempts = N`
    - Test file: `services/api/tests/test_retry.py`
    - **Validates: Requirements 9.2, 9.3**

  - [ ]* 2.5 Write property test for string truncation
    - **Property 6: String Truncation Preserves Prefix**
    - Generate strings of length 0–2000 with `hypothesis`; assert truncation at limits 256, 512, and 500 returns the input unchanged when `len ≤ limit`, and returns exactly the first K characters otherwise
    - Test file: `services/api/tests/test_retry.py` (or `test_observability.py`)
    - **Validates: Requirements 6.3, 6.4, 8.3**

- [x] 3. Create the Fallback Controller in `orchestration/fallbacks.py`

  Create `services/api/src/omni_modal/orchestration/fallbacks.py`:
  - Define `FallbackWarning` dataclass with fields: `source: str`, `reason: str`, `exception_type: str`
  - Define `OrchestrationResult` dataclass with fields: `response`, `warnings: list[FallbackWarning]`, `skipped_tools: list[dict[str, str]]`, `partial: bool`
  - Implement `FallbackController` class:
    - `handle_delegation_failure(exc, request_id) -> FallbackWarning` — captures exception via `observability.capture_exception` with `DelegationErrorContext`, returns `FallbackWarning(source="external_delegation", ...)`
    - `handle_retrieval_failure(exc, query) -> FallbackWarning` — returns `FallbackWarning(source="retrieval", ...)` with `partial=True` indicator
    - `handle_transcription_failure(exc, stage) -> FallbackWarning` — builds error message containing both the stage name and `type(exc).__name__`; returns `FallbackWarning(source="transcription", ...)`
    - `handle_tool_failure(exc, tool_name) -> dict[str, str]` — returns `{"name": tool_name, "reason": str(exc)}`
  - Export `FallbackController`, `FallbackWarning`, `OrchestrationResult` from `orchestration/__init__.py`
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6_

  - [ ]* 3.1 Write property test for fallback warning aggregation
    - **Property 11: Fallback Warning Aggregation**
    - Generate random subsets of `{"external_delegation", "retrieval", "transcription", "tool_call"}` as failing subsystems; assert `warnings` array contains exactly one entry per failed subsystem with the correct `source` identifier, and `skipped_tools` contains one entry per failed tool
    - Test file: `services/api/tests/test_fallbacks.py`
    - **Validates: Requirements 10.4, 10.6**

  - [ ]* 3.2 Write property test for transcription failure message completeness
    - **Property 12: Transcription Failure Message Completeness**
    - Generate `(stage, exception_type_name)` pairs; assert the resulting error message contains both the stage string and the exception type name string
    - Test file: `services/api/tests/test_fallbacks.py`
    - **Validates: Requirements 4.2, 10.2**

  - [ ]* 3.3 Write unit tests for FallbackController
    - External delegation fallback returns internal result only (no external findings) and populates `warnings` with `source="external_delegation"`
    - Retrieval failure returns `partial=True` and `failure_source="retrieval"` in response
    - Tool failure skips failed tool and includes `skipped_tools` entry with name and reason
    - Multiple fallback paths activated in one request — all warnings aggregated
    - Test file: `services/api/tests/test_fallbacks.py`
    - _Requirements: 10.1, 10.3, 10.4, 10.6_

- [x] 4. Checkpoint — Ensure all foundation tests pass
  - Run `python -m unittest discover -s services/api/tests` and confirm `test_pii_scrubbing.py`, `test_retry.py`, `test_observability.py`, and `test_fallbacks.py` all pass. Ask the user if any questions arise.

- [x] 5. Instrument the Ingestion Pipeline with Sentry breadcrumbs and error capture

  Modify `services/api/src/omni_modal/ingestion/pipeline.py` (and related ingestion modules):
  - At the start of each stage (extraction, normalization, chunking, embedding): call `observability.add_breadcrumb(message=f"Ingestion stage started: {stage}", category="ingestion", level="info", data={"stage": stage, "document_id": doc_id})`
  - On validation failure: call `observability.add_breadcrumb` at `"warning"` level with rejection reason, document_id, tenant_id, file_name, file_size, and source_type
  - On file extraction exception: call `observability.capture_exception(exc, operation="ingestion_extraction", context=IngestionErrorContext(...))`
  - On chunking exception: call `observability.capture_exception` with `IngestionErrorContext` including `chunk_index`
  - If multiple exceptions occur in a single ingestion request, capture each individually while sharing the same trace context
  - Wrap extraction and embedding outbound calls with `@retry_with_backoff` using `is_retryable` as the `retryable` predicate
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 9.9_

  - [ ]* 5.1 Write property test for ingestion observability context completeness
    - **Property 8: Ingestion Observability Context Completeness**
    - Generate random `(document_id, tenant_id, source_type, file_name, file_size)` tuples; simulate failures at each stage; assert the captured context includes all required fields, and that `chunk_index` is present when the failure stage is chunking
    - Test file: `services/api/tests/test_ingestion_observability.py`
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

  - [ ]* 5.2 Write unit tests for ingestion observability
    - Breadcrumb recorded at info level for each of the four stages
    - Validation failure produces a warning-level breadcrumb with all required fields
    - Chunking failure captures exception with correct `chunk_index`
    - Multiple exceptions in one request are each captured individually
    - Test file: `services/api/tests/test_ingestion_observability.py`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 6. Instrument the Transcription Service with Sentry breadcrumbs, error capture, and retry

  Modify the transcription service (under `services/api/src/omni_modal/ingestion/` or dedicated transcription module):
  - Record a breadcrumb at the start of each phase: model load, audio decode, transcription (phase name + timestamp)
  - On Whisper model load failure: capture exception with `TranscriptionErrorContext(model_name=..., available_memory_bytes=...)`
  - On audio decode failure: capture exception with `TranscriptionErrorContext(file_extension=..., file_size_bytes=..., audio_duration=...)`
  - On transcription timeout (> 300s default): capture timeout event with `elapsed_seconds` and `audio_duration`
  - If Sentry transmission itself fails, log locally and continue without interrupting the failure response
  - Wrap outbound transcription calls with `@retry_with_backoff(retryable=is_retryable)`
  - On `FallbackController.handle_transcription_failure`: mark document status as `"failed"` with error message containing stage and exception type; include status and message in upload response
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 9.9, 10.2_

- [x] 7. Instrument the Retrieval Service with Sentry spans, breadcrumbs, and retry

  Modify `services/api/src/omni_modal/qa/` (retrieval/vector search components):
  - Wrap the full retrieval operation with `observability.child_span("retrieval", "vector similarity search")` and add child spans for `embed`, `search`, and `rank` sub-operations
  - On embedding failure: capture exception with `RetrievalErrorContext(query_length=..., embedding_model=..., error_category="embedding_error")`
  - On vector search failure: capture exception with `RetrievalErrorContext(tenant_id=..., query_hash=..., top_k=..., failure_classification=...)` — classify as `"connection_error"` for `ConnectionError`/`TimeoutError`, `"query_error"` otherwise
  - On zero results: record breadcrumb with query text length, embedding model, filter criteria, and similarity threshold
  - On DB connection failure: capture exception with `RetrievalErrorContext(tenant_id=..., connection_timeout=..., db_host=...)`
  - Wrap DB connection and embedding calls with `@retry_with_backoff(retryable=is_retryable)`
  - On exhausted retries: delegate to `FallbackController.handle_retrieval_failure`; return response with `partial=True`, `failure_source="retrieval"`, and original query echoed
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 9.9, 10.3_

  - [ ]* 7.1 Write property test for retrieval failure classification
    - **Property 15: Retrieval Failure Classification**
    - Generate exceptions from connection-error and query-error categories; assert `failure_classification` is `"connection_error"` for connection/timeout types and `"query_error"` otherwise
    - Test file: `services/api/tests/test_ingestion_observability.py` (or new `test_retrieval_observability.py`)
    - **Validates: Requirements 5.2**

- [x] 8. Instrument MCP Tool Calls with Sentry breadcrumbs, error capture, timeout detection, and retry

  Modify `services/api/src/omni_modal/mcp/` tool execution path:
  - Before each tool call: record a breadcrumb at `"info"` level with tool name and PII-scrubbed key-value summary of parameters (truncated to 256 chars)
  - On tool exception: capture with `ToolCallErrorContext(tool_name=..., tenant_id=..., actor_user_id=..., scrubbed_params=...)` — apply `scrub_pii` before storing, truncate to 1024 chars
  - On tool timeout (> 30s): capture timeout event with tool name, `elapsed_ms`, and configured timeout value
  - On error response (status `"error"` or `"denied"`): record breadcrumb at `"warning"` level with tool name, error status, and message truncated to 512 chars
  - Apply `scrub_pii` to all tool call parameters before any Sentry transmission; redact non-primitive values and strings containing URLs, passwords, or secret tokens
  - Wrap tool calls with `@retry_with_backoff(retryable=is_retryable)`
  - On exhausted retries: `FallbackController.handle_tool_failure`; continue orchestration with remaining tools; include failed tool in `skipped_tools` array
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 9.9, 10.4_

- [x] 9. Instrument External Delegation with Sentry breadcrumbs, error capture, and retry

  Modify `services/api/src/omni_modal/orchestration/a2a.py` (and Gemini Interactions path):
  - Before each outbound request: record breadcrumb with endpoint host (host only, no path/query) and request ID
  - After response received: record breadcrumb with HTTP status code and response time in ms
  - On network error (connection refused, DNS failure, reset): capture with `DelegationErrorContext(endpoint_host=..., request_id=..., error_type=...)`
  - On timeout: capture with `DelegationErrorContext(endpoint_host=..., request_id=..., timeout_ms=..., elapsed_ms=...)`
  - On HTTP 4xx/5xx: capture with `DelegationErrorContext(endpoint_host=..., request_id=..., http_status=..., response_preview=body[:500])`
  - On JSON/schema parse error: capture with `DelegationErrorContext(request_id=..., response_preview=body[:500])`
  - Use `extract_host(url: str) -> str` helper that extracts scheme+host+port only (discard path, query, fragment)
  - Wrap HTTP calls with `@retry_with_backoff(retryable=is_retryable, respect_retry_after=True)`
  - On exhausted retries: `FallbackController.handle_delegation_failure`; return internal-only result with `warnings` field identifying `"external_delegation"` as unavailable
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 9.9, 10.1_

  - [ ]* 9.1 Write property test for URL host extraction
    - **Property 7: URL Host Extraction**
    - Generate valid URLs with varied schemes, hosts, ports, paths, queries, and fragments using `hypothesis`; assert `extract_host()` returns only `host` (or `host:port` for non-default ports) with no scheme, path, or query
    - Test file: `services/api/tests/test_observability.py` (or `test_fallbacks.py`)
    - **Validates: Requirements 8.1, 8.2, 8.4**

- [x] 10. Instrument Orchestration Timeouts and Step Breadcrumbs

  Modify `services/api/src/omni_modal/orchestration/adk_workflow.py` (and `phase1.py` as applicable):
  - At the start and completion of each workflow step: call `observability.add_breadcrumb` with step name, event type (`"start"` / `"complete"`), and timestamp
  - On step failure or interrupt: record breadcrumb with step name, `elapsed_ms`, and failure reason (no `"complete"` breadcrumb)
  - On step timeout (exceeds `Agent_Timeout`): capture exception with `AgentTimeoutContext(step_name=..., elapsed_ms=..., timeout_limit_ms=...)`
  - On overall workflow timeout: capture event with `AgentTimeoutContext` that includes `completed_steps: list[{name, duration_ms}]` and `in_progress_step` name
  - Wire `FallbackController` into the workflow so individual step failures activate the correct fallback path and aggregate warnings into `OrchestrationResult`
  - _Requirements: 7.1, 7.2, 7.3, 10.6_

  - [ ]* 10.1 Write property test for orchestration step breadcrumb symmetry
    - **Property 13: Orchestration Step Breadcrumb Symmetry**
    - Generate sequences of steps with randomized success/failure outcomes; assert each successful step produces exactly one `"start"` and one `"complete"` breadcrumb; each failed step produces a `"start"` breadcrumb and a `"failed"` breadcrumb (with `elapsed_ms` and reason), and no `"complete"` breadcrumb
    - Test file: `services/api/tests/test_orchestration_tracing.py`
    - **Validates: Requirements 7.2**

  - [ ]* 10.2 Write property test for orchestration timeout context completeness
    - **Property 14: Orchestration Timeout Context Completeness**
    - Generate lists of N completed steps (each with a name and duration) and one in-progress step; assert the captured timeout context includes all N completed steps with correct names/durations, the in-progress step name, `elapsed_ms`, and `timeout_limit_ms`
    - Test file: `services/api/tests/test_orchestration_tracing.py`
    - **Validates: Requirements 7.1, 7.3**

  - [ ]* 10.3 Write unit tests for orchestration tracing
    - Step breadcrumbs recorded in correct order for a multi-step workflow
    - Step timeout captures `AgentTimeoutContext` with correct fields
    - Overall timeout includes completed step list and in-progress step name
    - Test file: `services/api/tests/test_orchestration_tracing.py`
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 11. Checkpoint — Ensure all backend instrumentation tests pass
  - Run `python -m unittest discover -s services/api/tests` and confirm all new and existing tests pass. Ask the user if any questions arise.

- [x] 12. Initialize Sentry in the Next.js frontend

  Create `apps/web/src/lib/sentry.ts`:
  - Export `initSentry()` that reads `NEXT_PUBLIC_SENTRY_DSN`; returns early (no-op) if unset
  - If the DSN is set, validate its format with `new URL(dsn)` — on `TypeError`, log `console.warn("[Sentry] Malformed NEXT_PUBLIC_SENTRY_DSN, Sentry disabled.")` and return
  - On a valid DSN, call `Sentry.init({ dsn, environment: NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development", tracesSampleRate: 1.0, integrations: [Sentry.browserTracingIntegration()] })`
  - Update `apps/web/src/app/layout.tsx` to call `initSentry()` at module load
  - Add `@sentry/nextjs` to `apps/web/package.json` dependencies
  - Update `apps/web/next.config.ts` to wrap the config with `withSentryConfig` from `@sentry/nextjs`
  - _Requirements: 1.1, 1.6, 1.7, 1.8, 1.9_

- [x] 13. Create the Sentry Error Boundary component

  Create `apps/web/src/components/sentry-error-boundary.tsx`:
  - Import `ErrorBoundary` from `@sentry/nextjs`
  - Export a `SentryErrorBoundary` wrapper component with props `{ children: React.ReactNode; fallback?: React.ReactNode }`
  - Default fallback UI includes: a message indicating something went wrong, a "Try Again" button that calls `router.refresh()` (no full page reload), and a "Go Home" link navigating to `/`
  - Wrap the root layout children in `apps/web/src/app/layout.tsx` with `<SentryErrorBoundary>`
  - _Requirements: 1.5_

- [x] 14. Implement the instrumented API client fetch wrapper

  Create `apps/web/src/lib/api-client.ts`:
  - Export `apiRequest(path: string, init?: RequestInit, options?: ApiClientOptions): Promise<Response>`
  - Generate `X-Correlation-ID` via `crypto.randomUUID()` and attach as a request header
  - Read active Sentry span context via `Sentry.getActiveSpan()` and attach `sentry-trace` and `baggage` headers
  - Set an `AbortController` timeout (default: 30,000 ms); on timeout, treat as `"network_error"`
  - On non-2xx response or network error for upload requests: call `Sentry.captureException` asynchronously (non-blocking) with `{ file_name, file_size_bytes, http_status }` context
  - On non-2xx response or network error for research query requests: call `Sentry.captureException` asynchronously with `{ query_length, http_status }` context
  - On streaming disconnect (no chunk for > 15 s, or stream closes without end-of-stream signal): capture event with `{ total_bytes_received, elapsed_ms }`
  - Sentry capture must not block UI feedback by more than 50 ms (use `Promise` without `await` at call sites)
  - Export `ApiClientOptions` interface with `baseUrl?: string; timeout?: number`
  - Update existing API calls in `apps/web/src/components/upload-dropzone.tsx`, `research-chat.tsx`, and any other components that call the backend to use `apiRequest`
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 12.1_

  - [ ]* 14.1 Write property test for frontend outbound request headers
    - **Property 9: Frontend Outbound Request Headers**
    - Using `fast-check`, generate arbitrary request paths; assert every call through `apiRequest` attaches a valid UUID v4 `X-Correlation-ID`, a `sentry-trace` header, and a `baggage` header
    - Test file: `apps/web/src/__tests__/api-client.test.ts`
    - **Validates: Requirements 11.4, 12.1**

  - [ ]* 14.2 Write property test for frontend error capture context completeness
    - **Property 10: Frontend Error Capture Context Completeness**
    - Using `fast-check`, generate file metadata `(file_name, file_size)` and HTTP status codes (including the sentinel `"network_error"`); assert the Sentry capture context includes all applicable fields for the upload and research query cases
    - Test file: `apps/web/src/__tests__/error-capture.test.ts`
    - **Validates: Requirements 11.1, 11.2**

  - [ ]* 14.3 Write unit tests for the frontend API client
    - Upload failure (non-2xx) captures Sentry event with file_name, file_size, http_status
    - Research query failure (non-2xx) captures with query_length and http_status
    - Network error captures with `"network_error"` status
    - Streaming disconnect captures with `total_bytes_received` and `elapsed_ms`
    - Sentry capture is non-blocking (does not await before returning)
    - Test file: `apps/web/src/__tests__/api-client.test.ts`
    - _Requirements: 11.1, 11.2, 11.3, 11.5_

- [x] 15. Implement and test the Sentry initialization and error boundary in the frontend

  - [ ]* 15.1 Write unit tests for Sentry initialization
    - Sentry init with valid DSN — `Sentry.init` called with correct params
    - Sentry init with missing `NEXT_PUBLIC_SENTRY_DSN` — `Sentry.init` not called, no error thrown
    - Sentry init with malformed DSN — `console.warn` called with expected message, `Sentry.init` not called
    - Test file: `apps/web/src/__tests__/sentry-init.test.ts`
    - _Requirements: 1.1, 1.7, 1.9_

  - [ ]* 15.2 Write unit tests for error boundary component
    - Error boundary renders `children` when no error occurs
    - When a child component throws, boundary renders fallback UI
    - Fallback UI includes error message, "Try Again" button, and "Go Home" link
    - Test file: `apps/web/src/__tests__/error-boundary.test.tsx`
    - _Requirements: 1.5_

- [x] 16. Implement backend trace continuation in the HTTP handler and verify distributed tracing

  Modify `services/api/src/omni_modal/main.py` `OmniModalHandler`:
  - In `do_GET`, `do_POST`, and `_handle_query`: extract `sentry-trace` and `baggage` from `self.headers` and call `observability.continue_trace(headers)` at the top of each handler, wrapping the entire handler body as the transaction context
  - Call `observability.child_span(operation, description)` for each of: ingestion, transcription, retrieval, tool call, and delegation steps inside `_handle_query`
  - If `sentry-trace` / `baggage` headers are absent or malformed, `continue_trace` starts a new root transaction silently
  - If Sentry SDK is unavailable, all span/trace calls are no-ops
  - _Requirements: 12.2, 12.3, 12.4, 12.5, 12.6_

  - [ ]* 16.1 Write property test for backend trace continuation
    - **Property 16: Backend Trace Continuation**
    - Generate valid `sentry-trace` header values (format `{trace_id}-{span_id}-{sampled}`); assert the backend transaction's `trace_id` matches the incoming header's `trace_id`, establishing parent-child linkage
    - Test file: `services/api/tests/test_observability.py`
    - **Validates: Requirements 12.2**

  - [ ]* 16.2 Write unit tests for distributed tracing in the HTTP handler
    - Valid `sentry-trace` header → transaction linked to frontend span (trace_id matches)
    - Missing `sentry-trace` header → new root transaction started, no error raised
    - Malformed `sentry-trace` header → new root transaction started, no error raised
    - Sentry SDK unavailable → handler executes normally without raising
    - Test file: `services/api/tests/test_observability.py`
    - _Requirements: 12.2, 12.3, 12.5_

- [x] 17. Final checkpoint — Ensure all tests pass
  - Run `python -m unittest discover -s services/api/tests` (backend) and `npm run typecheck:web` (frontend) and confirm all new and existing tests pass. Ask the user if any questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP delivery
- Each task references specific requirements for full traceability
- Checkpoints (tasks 4, 11, 17) ensure incremental validation at logical milestones
- Property tests validate universal correctness properties using `hypothesis` (backend) and `fast-check` (frontend)
- Unit tests validate specific examples, edge cases, and integration behavior
- All Sentry calls must be guarded against SDK unavailability — `observability._sentry is None` checks must be preserved
- `@sentry/nextjs` must be added to `apps/web/package.json` before any frontend tasks can compile

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5"] },
    { "id": 2, "tasks": ["3.1", "3.2", "3.3"] },
    { "id": 3, "tasks": ["5.1", "5.2", "7.1", "9.1"] },
    { "id": 4, "tasks": ["10.1", "10.2", "10.3"] },
    { "id": 5, "tasks": ["14.1", "14.2", "14.3", "15.1", "15.2"] },
    { "id": 6, "tasks": ["16.1", "16.2"] }
  ]
}
```
