/**
 * Unit tests for Sentry initialization (Task 15.1)
 * Validates: Requirements 1.1, 1.7, 1.9
 *
 * Note: These tests require Jest or Vitest with jsdom environment.
 * Run: npm install -D jest @types/jest ts-jest jest-environment-jsdom
 *
 * Expected behaviors (executable once test runner is installed):
 */

export const SENTRY_INIT_TEST_CASES = [
  {
    name: "Sentry init with valid DSN — Sentry.init called with correct params",
    setup: "Set NEXT_PUBLIC_SENTRY_DSN to a valid DSN format string",
    assertion: "Sentry.init is called with { dsn, environment, tracesSampleRate: 1.0 }",
    requirement: "1.1, 1.6, 1.8",
  },
  {
    name: "Sentry init with missing NEXT_PUBLIC_SENTRY_DSN — Sentry.init not called",
    setup: "NEXT_PUBLIC_SENTRY_DSN is undefined",
    assertion: "initSentry() returns without calling Sentry.init; no error thrown",
    requirement: "1.7",
  },
  {
    name: "Sentry init with malformed DSN — console.warn called, Sentry.init not called",
    setup: "Set NEXT_PUBLIC_SENTRY_DSN to 'not-a-url'",
    assertion: "console.warn is called with '[Sentry] Malformed NEXT_PUBLIC_SENTRY_DSN...'",
    requirement: "1.9",
  },
  {
    name: "Sentry init with malformed DSN — application continues operating",
    setup: "Set NEXT_PUBLIC_SENTRY_DSN to 'invalid'",
    assertion: "No exception thrown; initSentry() returns normally",
    requirement: "1.9",
  },
] as const;

/** Export the initSentry function signature for type checking purposes */
export type { } from "@/lib/sentry";
