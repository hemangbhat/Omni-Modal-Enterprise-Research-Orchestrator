# Requirements Document

## Introduction

This feature integrates Sentry observability across the Next.js frontend and Python backend of the Omni-Modal Enterprise Research Orchestrator. The goal is to provide full-stack error tracing, actionable failure context, and intelligent retry/fallback logic for critical operations including uploads, transcription, retrieval, tool calls, agent timeouts, and external delegation failures. The system must surface failures transparently while recovering gracefully where appropriate.

## Glossary

- **Frontend**: The Next.js App Router application located in `apps/web`
- **Backend**: The Python HTTP orchestration service located in `services/api`
- **Sentry**: Third-party error tracking and performance monitoring platform (sentry.io)
- **Sentry_SDK**: The client library that captures and transmits error/performance data to Sentry
- **Ingestion_Pipeline**: The Backend subsystem that handles document uploads, text extraction, and chunking
- **Transcription_Service**: The Backend subsystem that converts audio files to text using Whisper
- **Retrieval_Service**: The Backend subsystem that performs vector-similarity searches against document embeddings
- **Tool_Call**: An invocation of an MCP-backed tool or internal data access operation during orchestration
- **Agent_Timeout**: A condition where an orchestration step exceeds its maximum allowed execution time
- **External_Delegation**: An A2A (Agent-to-Agent) or Gemini Interactions API call to an external research service
- **Retry_Policy**: A configuration specifying how many times an operation is retried, with what backoff strategy, and under which error conditions
- **Fallback_Path**: An alternative execution path taken when the primary operation fails after all retries are exhausted
- **Breadcrumb**: A Sentry concept representing a timestamped event that provides context leading up to an error
- **Trace**: A distributed performance trace linking related operations across frontend and backend
- **PII_Scrubbing**: The process of removing personally identifiable information from error payloads before transmission to Sentry

## Requirements

### Requirement 1: Frontend Sentry Integration

**User Story:** As a platform operator, I want the Next.js frontend to report unhandled errors and performance data to Sentry, so that I can diagnose client-side failures without relying on user reports.

#### Acceptance Criteria

1. WHEN the Frontend application starts, THE Sentry_SDK SHALL initialize using the `NEXT_PUBLIC_SENTRY_DSN` environment variable
2. WHEN an unhandled JavaScript error occurs in the Frontend, THE Sentry_SDK SHALL capture and transmit the error with a stack trace to Sentry within 10 seconds of occurrence
3. WHEN a server-side rendering error occurs, THE Sentry_SDK SHALL capture the error with request context attached, including the request URL, HTTP method, and request headers
4. WHEN an API route handler in the Frontend throws an exception, THE Sentry_SDK SHALL capture the error with the route path as a tag
5. THE Frontend SHALL include a Sentry error boundary component that captures React rendering errors and displays a fallback UI containing an error indication message and a recovery action that allows the user to retry or navigate away without a full page reload
6. THE Frontend SHALL attach the `NEXT_PUBLIC_SENTRY_ENVIRONMENT` value as the Sentry environment tag
7. IF the `NEXT_PUBLIC_SENTRY_DSN` variable is not set, THEN THE Frontend SHALL start without initializing the Sentry_SDK and SHALL not throw errors or degrade functionality due to the missing configuration
8. WHEN the Frontend application starts with a valid `NEXT_PUBLIC_SENTRY_DSN`, THE Sentry_SDK SHALL enable performance tracing and capture transaction data for page loads and navigations
9. IF the `NEXT_PUBLIC_SENTRY_DSN` variable is set but contains a malformed value, THEN THE Frontend SHALL log a warning to the browser console and SHALL continue operating without Sentry instrumentation

### Requirement 2: Backend Sentry Integration Enhancement

**User Story:** As a platform operator, I want the Python backend to provide structured error context for every captured exception, so that I can quickly identify the root cause of failures.

#### Acceptance Criteria

1. WHEN the Backend starts and the Sentry DSN environment variable is set, THE Sentry_SDK SHALL initialize with performance tracing enabled at the configured sample rate (a decimal value between 0.0 and 1.0 inclusive)
2. IF the Sentry DSN environment variable is not set, THEN THE Backend SHALL operate normally without Sentry instrumentation and SHALL log a warning at startup indicating that Sentry is disabled
3. IF the request scope contains an authenticated user context, THEN THE Backend SHALL attach the `tenant_id` and `user_id` as Sentry tags on that request scope
4. WHEN the Backend captures an exception, THE Sentry_SDK SHALL include the operation name, request path, and applicable domain identifiers (`tenant_id`, `document_id`, `query_id`, or `tool_name`) as structured context
5. THE Backend SHALL apply PII_Scrubbing to all Sentry payloads before transmission, removing passwords, tokens, connection strings, and email addresses from extra data, breadcrumb messages, and tag values
6. WHEN the Backend shuts down, THE Sentry_SDK SHALL flush pending events within a 2-second timeout and IF the flush does not complete within the timeout, THEN THE Backend SHALL proceed with shutdown without raising an error

### Requirement 3: Upload Error Capture

**User Story:** As a platform operator, I want upload failures to produce actionable Sentry traces, so that I can determine whether failures stem from validation, file I/O, or downstream processing.

#### Acceptance Criteria

1. WHEN a document upload fails validation, THE Ingestion_Pipeline SHALL capture a Sentry breadcrumb at "warning" level including the rejection reason, document ID, tenant ID, file name, file size in bytes, and inferred source type
2. WHEN a file extraction operation (PDF or audio) raises an exception, THE Ingestion_Pipeline SHALL capture the exception to Sentry with the document ID, tenant ID, source type, and file size in bytes as structured context
3. WHEN a chunking operation fails, THE Ingestion_Pipeline SHALL capture the exception to Sentry with the document ID, tenant ID, source type, and the zero-based chunk index at which the failure occurred as structured context
4. THE Ingestion_Pipeline SHALL record a breadcrumb at "info" level at the start of each processing stage (extraction, normalization, chunking, embedding) including the stage name and document ID
5. IF multiple exceptions occur during a single ingestion request, THEN THE Ingestion_Pipeline SHALL capture each exception individually while preserving the shared trace context of that request

### Requirement 4: Transcription Error Capture

**User Story:** As a platform operator, I want transcription failures to be traced with sufficient detail, so that I can distinguish between model loading errors, audio format issues, and resource exhaustion.

#### Acceptance Criteria

1. WHEN the Transcription_Service fails to load the Whisper model, THE Backend SHALL capture the exception to Sentry with the model name and available system memory in bytes at the time of failure as context
2. WHEN audio decoding fails, THE Transcription_Service SHALL capture the exception with the file extension, file byte size, and audio duration from file metadata (or "unknown" if metadata is unreadable) as context
3. WHEN transcription processing time exceeds the configured timeout (default: 300 seconds), THE Transcription_Service SHALL capture a timeout event to Sentry with the elapsed time in seconds and the audio duration from file metadata as context
4. THE Transcription_Service SHALL record a breadcrumb at the start of each processing phase (model load, audio decode, transcription), including the phase name and a timestamp
5. IF Sentry event transmission fails during transcription error capture, THEN THE Transcription_Service SHALL log the error locally and continue operation without interrupting the failure response to the caller

### Requirement 5: Retrieval Error Capture

**User Story:** As a platform operator, I want retrieval failures to be traced, so that I can identify whether issues originate from embedding generation, database connectivity, or query construction.

#### Acceptance Criteria

1. WHEN the embedding generation for a query fails, THE Retrieval_Service SHALL capture the exception with the query length in characters, embedding model name, and error category as context
2. WHEN the vector similarity search query fails, THE Retrieval_Service SHALL capture the exception with the tenant ID, query hash, requested result count, and failure classification (connection error or query error) as context
3. WHEN the Retrieval_Service returns zero results for a query, THE Retrieval_Service SHALL record a Sentry breadcrumb with the query text length, embedding model used, applied filter criteria, and similarity threshold as context
4. THE Retrieval_Service SHALL create a Sentry performance span wrapping the full retrieval operation, with child spans for each sub-operation (embed, search, rank)
5. WHEN the vector database connection fails before a similarity search can execute, THE Retrieval_Service SHALL capture the exception with the tenant ID, connection timeout duration, and database host identifier as context

### Requirement 6: Tool Call Error Capture

**User Story:** As a platform operator, I want MCP tool call failures traced with tool identity and invocation parameters, so that I can identify broken tool integrations quickly.

#### Acceptance Criteria

1. WHEN an MCP Tool_Call raises an exception, THE Backend SHALL capture the exception to Sentry with the tool name, PII-scrubbed invocation parameters (non-primitive values redacted, strings containing URLs or secrets redacted, truncated to 1024 characters), tenant_id, and actor_user_id as structured context
2. WHEN an MCP Tool_Call exceeds the configured timeout (default: 30 seconds), THE Backend SHALL capture the timeout event to Sentry with the tool name, elapsed time in milliseconds, and the configured timeout value as context
3. WHEN an MCP Tool_Call is about to be invoked, THE Backend SHALL record a Sentry breadcrumb at info level with the tool name and a PII-scrubbed key-value summary of parameters truncated to 256 characters
4. WHEN a Tool_Call returns an error response (non-exception status of "error" or "denied"), THE Backend SHALL record a Sentry breadcrumb at warning level with the tool name, error status, and error message truncated to 512 characters
5. THE Backend SHALL apply PII_Scrubbing to all Tool_Call observability payloads by redacting non-primitive parameter values, and redacting string values that contain URLs, passwords, or secret tokens before transmission to Sentry

### Requirement 7: Agent Timeout Capture

**User Story:** As a platform operator, I want agent orchestration timeouts to produce detailed traces, so that I can identify which pipeline step is the bottleneck.

#### Acceptance Criteria

1. WHEN an orchestration workflow step exceeds the configured Agent_Timeout, THE Backend SHALL capture a timeout exception to Sentry with the step name, elapsed time in milliseconds, and configured timeout limit in milliseconds as context
2. THE Backend SHALL record a breadcrumb at the start and completion of each orchestration workflow step, including the step name and a timestamp; IF a step fails or is interrupted before completion, THEN THE Backend SHALL record a breadcrumb with the step name, elapsed time in milliseconds, and failure reason
3. WHEN the overall orchestration workflow exceeds its total timeout, THE Backend SHALL capture the event with a trace of all completed steps and their durations in milliseconds, and SHALL include the name and elapsed time of the in-progress step that was executing when the timeout occurred

### Requirement 8: External Delegation Failure Capture

**User Story:** As a platform operator, I want external delegation failures (A2A, Gemini Interactions) to be traced with request/response context, so that I can distinguish between network errors, timeouts, and malformed responses.

#### Acceptance Criteria

1. WHEN an External_Delegation HTTP request fails due to a network error, THE Backend SHALL capture the exception with the endpoint URL (host only), request ID, and error type (connection refused, DNS resolution failure, or connection reset) as context
2. WHEN an External_Delegation HTTP request times out, THE Backend SHALL capture the timeout event with the endpoint URL (host only), request ID, configured timeout value, and elapsed time as context
3. WHEN an External_Delegation response contains invalid JSON or fails schema validation (missing required fields or type mismatches against the expected response contract), THE Backend SHALL capture the parse error with the request ID and a truncated response preview (first 500 characters) as context
4. WHEN an External_Delegation HTTP response returns an error status code (4xx or 5xx), THE Backend SHALL capture the error with the endpoint URL (host only), request ID, HTTP status code, and a truncated response body (first 500 characters) as context
5. THE Backend SHALL record breadcrumbs for delegation request start (including endpoint host and request ID) and response received (including HTTP status code and response time in milliseconds) events

### Requirement 9: Retry Logic for Transient Failures

**User Story:** As a platform operator, I want the system to retry transient failures with exponential backoff, so that temporary network issues or service hiccups do not cause permanent user-facing errors.

#### Acceptance Criteria

1. THE Retry_Policy SHALL use exponential backoff with a configurable base delay defaulting to 1000 milliseconds, a maximum retry count defaulting to 3 attempts, and a maximum total delay defaulting to 30 seconds
2. WHEN a retryable exception occurs, THE Backend SHALL record a Sentry breadcrumb for each retry attempt with the attempt number, delay, and exception type
3. WHEN all retry attempts are exhausted, THE Backend SHALL capture the final exception to Sentry with the total attempt count and cumulative elapsed time as context
4. THE Retry_Policy SHALL classify the following as retryable: network connection errors, HTTP 429 (rate limit) responses, HTTP 502/503/504 (gateway) responses, and database connection timeouts
5. THE Retry_Policy SHALL classify the following as non-retryable: HTTP 400 (bad request) responses, HTTP 401/403 (auth) responses, validation errors, and file format errors
6. WHEN a retry succeeds after one or more failures, THE Backend SHALL record a Sentry breadcrumb indicating recovery with the total attempts used
7. THE Retry_Policy SHALL add random jitter of up to 25% of the computed delay to each backoff interval to prevent synchronized retry storms
8. WHEN an HTTP 429 response includes a Retry-After header, THE Retry_Policy SHALL use the Retry-After value as the next retry delay instead of the computed exponential backoff, provided the value does not exceed the maximum total delay
9. THE Retry_Policy SHALL apply to outbound operations in the Transcription_Service, Retrieval_Service, External_Delegation, and Tool_Call subsystems

### Requirement 10: Fallback Paths for Critical Operations

**User Story:** As a platform operator, I want the system to degrade gracefully when primary operations fail, so that users receive partial results rather than complete failures.

#### Acceptance Criteria

1. WHEN External_Delegation fails after retries, THE Backend SHALL return the internal research result without external findings and include a structured warning field in the response payload that identifies "external_delegation" as the unavailable data source
2. WHEN the Transcription_Service fails after retries, THE Ingestion_Pipeline SHALL mark the document status as "failed" with an error message containing the failure stage (model load, decode, or timeout) and the exception type, and SHALL include this status and message in the upload response payload
3. WHEN the Retrieval_Service fails during a research query, THE Backend SHALL return a response with a partial-result indicator field set to true, a failure-source field identifying "retrieval", and the original query text echoed back, rather than a generic server error
4. WHEN a Tool_Call fails after retries, THE Backend SHALL skip the failed tool result, continue orchestration using only the results from successfully completed tools, and include the skipped tool name and failure reason in a structured "skipped_tools" array within the response payload
5. IF the Sentry_SDK itself fails to initialize or transmit, THEN THE Backend SHALL continue all non-observability operations without interruption and SHALL log the Sentry failure to the application's local logging output
6. WHEN multiple fallback paths activate within a single orchestration request, THE Backend SHALL aggregate all warning indicators into the response and SHALL return partial results as long as at least the internal research result is available

### Requirement 11: Frontend Error Capture for User Operations

**User Story:** As a platform operator, I want frontend user-facing operations (upload, research query) to report failures to Sentry with user action context, so that I can correlate backend failures with the user's perspective.

#### Acceptance Criteria

1. WHEN a file upload request from the Frontend receives a non-2xx HTTP response or a network error (connection refused, DNS failure, or request timeout exceeding 30 seconds), THE Frontend SHALL capture the error to Sentry with the file name, file size in bytes, and HTTP status code (or "network_error" if no response was received) as context
2. WHEN a research query request from the Frontend receives a non-2xx HTTP response or a network error (connection refused, DNS failure, or request timeout exceeding 30 seconds), THE Frontend SHALL capture the error to Sentry with the query length in characters and HTTP status code (or "network_error" if no response was received) as context
3. WHEN a streaming response from the Backend closes without sending an end-of-stream signal, or when no data chunk is received for more than 15 seconds during an active stream, THE Frontend SHALL capture the disconnect event to Sentry with the total bytes received and elapsed time in milliseconds as context
4. THE Frontend SHALL attach a correlation ID header named "X-Correlation-ID" containing a UUID v4 value to every API request, linking frontend and backend Sentry traces
5. WHEN the Frontend captures an error to Sentry, THE Frontend SHALL perform the capture asynchronously so that the Sentry reporting does not block user interaction or delay UI feedback by more than 50 milliseconds

### Requirement 12: Distributed Tracing Across Frontend and Backend

**User Story:** As a platform operator, I want end-to-end distributed traces linking frontend user actions to backend processing, so that I can measure latency across the full request path.

#### Acceptance Criteria

1. THE Frontend SHALL include `sentry-trace` and `baggage` headers on every outbound API request to the Backend
2. WHEN the Backend receives a request with valid `sentry-trace` and `baggage` headers, THE Backend SHALL continue the trace context from the incoming headers and create a transaction linked to the frontend span
3. IF the Backend receives a request with missing or malformed Sentry trace headers, THEN THE Backend SHALL start a new root transaction and process the request without error
4. THE Backend SHALL create a child span for each of the following processing steps: ingestion, transcription, retrieval, tool call, and delegation, where each span includes the operation name and is nested under the request transaction
5. IF the Sentry SDK is unavailable or uninitialized, THEN THE Backend SHALL process requests normally without emitting spans and without raising errors to the caller
6. WHEN a request transaction completes, THE Backend SHALL have emitted a trace containing the root transaction span and one child span per executed processing step, such that the trace is available for inspection in the configured Sentry project
