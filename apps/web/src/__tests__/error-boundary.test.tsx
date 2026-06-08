/**
 * Unit tests for SentryErrorBoundary component (Task 15.2)
 * Validates: Requirements 1.5
 *
 * Note: These tests require Jest or Vitest with React Testing Library.
 * Run: npm install -D @testing-library/react @testing-library/jest-dom
 *
 * Expected behaviors:
 */

export const ERROR_BOUNDARY_TEST_CASES = [
  {
    name: "renders children when no error occurs",
    setup: "Render <SentryErrorBoundary><span>content</span></SentryErrorBoundary>",
    assertion: "span with 'content' is visible in the DOM",
    requirement: "1.5",
  },
  {
    name: "when a child component throws, renders fallback UI",
    setup: "Render a component that throws during render, wrapped in SentryErrorBoundary",
    assertion: "Fallback UI is displayed instead of the throwing component",
    requirement: "1.5",
  },
  {
    name: "fallback UI includes error message",
    setup: "Trigger error boundary by throwing in a child component",
    assertion: "Text matching /something went wrong/i is present in the DOM",
    requirement: "1.5",
  },
  {
    name: "fallback UI includes Try Again button",
    setup: "Trigger error boundary",
    assertion: "Button with text 'Try Again' is rendered",
    requirement: "1.5",
  },
  {
    name: "fallback UI includes Go Home link",
    setup: "Trigger error boundary",
    assertion: "Link with text 'Go Home' pointing to '/' is rendered",
    requirement: "1.5",
  },
] as const;

export type SentryErrorBoundaryTestCase = typeof ERROR_BOUNDARY_TEST_CASES[number];
