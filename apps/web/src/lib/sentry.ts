/**
 * Sentry initialization for the Omni-Modal frontend.
 *
 * Requirements: 1.1, 1.6, 1.7, 1.8, 1.9
 *
 * - If NEXT_PUBLIC_SENTRY_DSN is not set, returns without error (Req 1.7)
 * - If DSN is malformed, logs console.warn and returns (Req 1.9)
 * - If DSN is valid, initializes Sentry SDK with performance tracing (Req 1.1, 1.8)
 */
export function initSentry(): void {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

  // Req 1.7: Missing DSN — start without Sentry, no error
  if (!dsn) return;

  // Req 1.9: Validate DSN format
  try {
    new URL(dsn);
  } catch {
    console.warn("[Sentry] Malformed NEXT_PUBLIC_SENTRY_DSN, Sentry disabled.");
    return;
  }

  // Dynamic import to avoid hard dependency when SDK is not installed
  // @ts-ignore — @sentry/nextjs is an optional peer dependency
  void import("@sentry/nextjs")
    .then((Sentry) => {
      Sentry.init({
        dsn,
        // Req 1.6: Attach environment tag
        environment:
          process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
        // Req 1.8: Enable performance tracing
        tracesSampleRate: 1.0,
        integrations: [Sentry.browserTracingIntegration()],
      });
    })
    .catch(() => {
      // @sentry/nextjs not installed — silently ignore
    });
}
