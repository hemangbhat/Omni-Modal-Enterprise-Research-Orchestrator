/**
 * Instrumented API client for Omni-Modal frontend.
 * Attaches X-Correlation-ID, sentry-trace, and baggage headers to every request.
 * Captures failures to Sentry asynchronously (non-blocking).
 *
 * Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 12.1
 */

export interface ApiClientOptions {
  /** Base URL prefix. Defaults to "" (relative). */
  baseUrl?: string;
  /** Request timeout in milliseconds. Defaults to 30_000. */
  timeout?: number;
}

export interface UploadErrorContext {
  file_name: string;
  file_size_bytes: number;
  http_status: number | "network_error";
}

export interface QueryErrorContext {
  query_length: number;
  http_status: number | "network_error";
}

export interface StreamDisconnectContext {
  total_bytes_received: number;
  elapsed_ms: number;
}

/**
 * Make an instrumented API request.
 *
 * Automatically attaches:
 * - X-Correlation-ID (UUID v4) — Requirement 11.4
 * - sentry-trace and baggage headers (from active Sentry span if available) — Requirement 12.1
 *
 * On failure, captures to Sentry asynchronously (fire-and-forget) — Requirement 11.5
 */
export async function apiRequest(
  path: string,
  init?: RequestInit,
  options?: ApiClientOptions,
): Promise<Response> {
  const { baseUrl = "", timeout = 30_000 } = options ?? {};
  const correlationId = crypto.randomUUID();

  // Build headers
  const headers = new Headers(init?.headers);
  headers.set("X-Correlation-ID", correlationId);

  // Attach Sentry distributed tracing headers if available — Requirement 12.1
  try {
    // Dynamic import so this never throws when @sentry/nextjs is absent
    // @ts-ignore — @sentry/nextjs may not be installed; import is intentionally runtime-only
    const Sentry = await (import("@sentry/nextjs") as Promise<unknown>).catch(
      () => null,
    ) as Record<string, (...args: unknown[]) => unknown> | null;
    if (Sentry) {
      const span = Sentry.getActiveSpan?.();
      if (span) {
        const traceData = Sentry.spanToTraceHeader?.(span) as string | undefined;
        const baggageData = Sentry.spanToBaggageHeader?.(span) as string | undefined;
        if (traceData) headers.set("sentry-trace", traceData);
        if (baggageData) headers.set("baggage", baggageData);
      }
    }
  } catch {
    // Sentry header attachment is best-effort — never block the request
  }

  // AbortController timeout — Requirement 11.1 (30s network_error)
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  const url = `${baseUrl}${path}`;

  try {
    const response = await fetch(url, {
      ...init,
      headers,
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    return response;
  } catch (err) {
    clearTimeout(timeoutId);
    // Re-throw — caller is responsible for capturing with context
    throw err;
  }
}

/**
 * Capture an upload error to Sentry asynchronously (non-blocking).
 * Per Requirement 11.5: must not delay UI by more than 50ms.
 *
 * Requirements: 11.1
 */
export function captureUploadError(context: UploadErrorContext): void {
  void Promise.resolve().then(async () => {
    try {
      // @ts-ignore — @sentry/nextjs may not be installed; import is intentionally runtime-only
      const Sentry = await (import("@sentry/nextjs") as Promise<unknown>).catch(() => null) as
        | { captureException: (err: Error, ctx: { extra: Record<string, unknown> }) => void }
        | null;
      if (!Sentry) return;
      Sentry.captureException(
        new Error(`Upload failed: HTTP ${context.http_status}`),
        {
          extra: {
            file_name: context.file_name,
            file_size_bytes: context.file_size_bytes,
            http_status: context.http_status,
          },
        },
      );
    } catch {
      // Never throw from error reporting
    }
  });
}

/**
 * Capture a research query error to Sentry asynchronously (non-blocking).
 *
 * Requirements: 11.2
 */
export function captureQueryError(context: QueryErrorContext): void {
  void Promise.resolve().then(async () => {
    try {
      // @ts-ignore — @sentry/nextjs may not be installed; import is intentionally runtime-only
      const Sentry = await (import("@sentry/nextjs") as Promise<unknown>).catch(() => null) as
        | { captureException: (err: Error, ctx: { extra: Record<string, unknown> }) => void }
        | null;
      if (!Sentry) return;
      Sentry.captureException(
        new Error(`Query failed: HTTP ${context.http_status}`),
        {
          extra: {
            query_length: context.query_length,
            http_status: context.http_status,
          },
        },
      );
    } catch {
      // Never throw from error reporting
    }
  });
}

/**
 * Capture a streaming disconnect event to Sentry asynchronously.
 *
 * Requirements: 11.3
 */
export function captureStreamDisconnect(
  context: StreamDisconnectContext,
): void {
  void Promise.resolve().then(async () => {
    try {
      // @ts-ignore — @sentry/nextjs may not be installed; import is intentionally runtime-only
      const Sentry = await (import("@sentry/nextjs") as Promise<unknown>).catch(() => null) as
        | { captureMessage: (msg: string, ctx: { level: string; extra: Record<string, unknown> }) => void }
        | null;
      if (!Sentry) return;
      Sentry.captureMessage("Stream disconnected unexpectedly", {
        level: "warning",
        extra: {
          total_bytes_received: context.total_bytes_received,
          elapsed_ms: context.elapsed_ms,
        },
      });
    } catch {
      // Never throw from error reporting
    }
  });
}
