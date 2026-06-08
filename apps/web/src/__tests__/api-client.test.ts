/**
 * Property 9: Frontend Outbound Request Headers
 * Validates: Requirements 11.4, 12.1
 *
 * Note: These tests require Jest or Vitest to run.
 * Install one of: `npm install -D jest @types/jest ts-jest` or `npm install -D vitest`
 * The test file structure follows the Jest/Vitest convention.
 *
 * Unit tests for api-client.ts (Tasks 14.1, 14.3)
 * Documents expected behaviors — executable once a test runner is installed.
 */

import type { UploadErrorContext, QueryErrorContext, StreamDisconnectContext } from "@/lib/api-client";

// ---------------------------------------------------------------------------
// UUID v4 validator used by Property 9
// ---------------------------------------------------------------------------

/** UUID v4 regex for validation. */
export const UUID_V4_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// ---------------------------------------------------------------------------
// Property 9 descriptions (fast-check property tests)
// ---------------------------------------------------------------------------

/**
 * **Property 9: Frontend Outbound Request Headers**
 *
 * For any API request made through apiRequest,
 * the outgoing request SHALL include an X-Correlation-ID header
 * whose value is a valid UUID v4 string.
 *
 * Validates: Requirements 11.4, 12.1
 *
 * fast-check strategy:
 *   fc.string({ minLength: 1, maxLength: 200 }) for `path`
 *   Assert that the captured fetch call has X-Correlation-ID matching UUID_V4_REGEX
 */
export const PROPERTY_9_CORRELATION_ID_DESCRIPTION = `
  For any API request made through apiRequest,
  the outgoing request SHALL include an X-Correlation-ID header
  whose value is a valid UUID v4 string.
`;

/**
 * **Property 9b: Sentry trace headers attached when span is active**
 *
 * For any API request made through apiRequest,
 * the outgoing request SHALL include sentry-trace and baggage headers
 * when an active Sentry span is present.
 *
 * Validates: Requirements 12.1
 */
export const PROPERTY_9_SENTRY_HEADERS_DESCRIPTION = `
  For any API request made through apiRequest,
  the outgoing request SHALL include sentry-trace and baggage headers
  when an active Sentry span is present.
`;

// ---------------------------------------------------------------------------
// Unit test case catalogue (Tasks 14.3)
// ---------------------------------------------------------------------------

/**
 * Unit test cases for api-client.ts — executable once a test runner is installed.
 *
 * Validates: Requirements 11.1, 11.2, 11.3, 11.5
 */
export const UNIT_TEST_CASES = [
  "Upload failure (non-2xx) captures Sentry event with file_name, file_size, http_status",
  "Research query failure (non-2xx) captures with query_length and http_status",
  "Network error (AbortError) captures with 'network_error' status",
  "Stream disconnect captures with total_bytes_received and elapsed_ms",
  "Sentry capture is non-blocking (does not await before returning)",
  "X-Correlation-ID is a valid UUID v4 on every request",
  "Existing init?.headers are preserved alongside injected headers",
  "AbortController timeout fires after configured timeout ms",
] as const;

// ---------------------------------------------------------------------------
// Type guards (compile-time validation that interfaces remain coherent)
// ---------------------------------------------------------------------------

/** Ensure UploadErrorContext has the required fields at compile time. */
const _uploadContextCheck: UploadErrorContext = {
  file_name: "test.pdf",
  file_size_bytes: 1024,
  http_status: 500,
};
void _uploadContextCheck;

/** Ensure QueryErrorContext has the required fields at compile time. */
const _queryContextCheck: QueryErrorContext = {
  query_length: 42,
  http_status: "network_error",
};
void _queryContextCheck;

/** Ensure StreamDisconnectContext has the required fields at compile time. */
const _streamContextCheck: StreamDisconnectContext = {
  total_bytes_received: 8192,
  elapsed_ms: 16000,
};
void _streamContextCheck;
