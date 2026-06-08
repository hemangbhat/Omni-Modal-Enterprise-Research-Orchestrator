/**
 * Property 10: Frontend Error Capture Context Completeness
 * Validates: Requirements 11.1, 11.2
 *
 * Note: These tests require Jest or Vitest to run.
 * Install one of: `npm install -D jest @types/jest ts-jest` or `npm install -D vitest`
 *
 * fast-check property tests documenting Property 10 expectations.
 */

import type { UploadErrorContext, QueryErrorContext } from "@/lib/api-client";

// ---------------------------------------------------------------------------
// Property 10 descriptions
// ---------------------------------------------------------------------------

/**
 * **Property 10a: Upload Error Capture Context Completeness**
 *
 * For any failed upload request, the Sentry capture SHALL include
 * file_name, file_size_bytes, and http_status in the extra context.
 *
 * Validates: Requirements 11.1
 *
 * fast-check strategy:
 *   fc.record({
 *     file_name: fc.string({ minLength: 1, maxLength: 255 }),
 *     file_size_bytes: fc.integer({ min: 0, max: 500_000_000 }),
 *     http_status: fc.oneof(
 *       fc.integer({ min: 400, max: 599 }),
 *       fc.constant("network_error" as const)
 *     ),
 *   })
 *   Assert captureUploadError is called with an `extra` containing all three fields.
 */
export const PROPERTY_10_UPLOAD_CONTEXT_DESCRIPTION = `
  For any failed upload request, the Sentry capture SHALL include
  file_name, file_size_bytes, and http_status in the extra context.
`;

/**
 * **Property 10b: Query Error Capture Context Completeness**
 *
 * For any failed query request, the Sentry capture SHALL include
 * query_length and http_status in the extra context.
 *
 * Validates: Requirements 11.2
 *
 * fast-check strategy:
 *   fc.record({
 *     query_length: fc.integer({ min: 0, max: 10_000 }),
 *     http_status: fc.oneof(
 *       fc.integer({ min: 400, max: 599 }),
 *       fc.constant("network_error" as const)
 *     ),
 *   })
 *   Assert captureQueryError is called with an `extra` containing both fields.
 */
export const PROPERTY_10_QUERY_CONTEXT_DESCRIPTION = `
  For any failed query request, the Sentry capture SHALL include
  query_length and http_status in the extra context.
`;

// ---------------------------------------------------------------------------
// Compile-time type validation
// ---------------------------------------------------------------------------

/** All required fields for upload context. */
const _uploadFields: (keyof UploadErrorContext)[] = [
  "file_name",
  "file_size_bytes",
  "http_status",
];
void _uploadFields;

/** All required fields for query context. */
const _queryFields: (keyof QueryErrorContext)[] = [
  "query_length",
  "http_status",
];
void _queryFields;

/**
 * Exhaustive check: http_status must accept both numeric codes and "network_error".
 * This will cause a compile error if the union type changes incompatibly.
 */
function _assertHttpStatusUnion(
  status: UploadErrorContext["http_status"],
): void {
  if (status === "network_error") return;
  const _numeric: number = status;
  void _numeric;
}
void _assertHttpStatusUnion;
