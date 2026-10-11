/**
 * Reads an error response body from the backend.
 *
 * The FastAPI backend reports errors under a `detail` key that is either a
 * string, an object (e.g. `{ error, metric, limit }`), or — for request
 * validation failures (HTTP 422) — a list of `{ msg }` items. Older responses
 * used a top-level `error` string. This normalises all of them so callers can
 * show the backend's own message instead of a bare status code.
 */
export type ApiError = {
  /** Human-readable message, or null when the body carried none. */
  message: string | null;
  /** Structured fields sent alongside the message (empty when none). */
  fields: Record<string, unknown>;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseApiError(body: unknown): ApiError {
  if (!isRecord(body)) return { message: null, fields: {} };
  const detail = body.detail;
  if (typeof detail === "string") return { message: detail, fields: {} };
  if (Array.isArray(detail)) {
    const first = detail.find((item) => isRecord(item) && typeof item.msg === "string");
    return { message: first ? String((first as Record<string, unknown>).msg) : null, fields: {} };
  }
  const source = isRecord(detail) ? detail : body;
  return {
    message: typeof source.error === "string" ? source.error : null,
    fields: source
  };
}
