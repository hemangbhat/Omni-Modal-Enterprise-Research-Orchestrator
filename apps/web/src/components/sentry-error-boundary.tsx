"use client";

/**
 * Sentry-aware React error boundary for the Omni-Modal frontend.
 *
 * Requirements: 1.5
 *
 * - Captures React rendering errors to Sentry
 * - Displays a fallback UI with a "Try Again" button (router.refresh) and "Go Home" link
 * - No full page reload on retry
 */
import React from "react";
import { useRouter } from "next/navigation";

interface SentryErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  eventId: string | null;
}

class ErrorBoundaryInner extends React.Component<
  SentryErrorBoundaryProps & { router: ReturnType<typeof useRouter> },
  ErrorBoundaryState
> {
  constructor(
    props: SentryErrorBoundaryProps & { router: ReturnType<typeof useRouter> }
  ) {
    super(props);
    this.state = { hasError: false, eventId: null };
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true, eventId: null };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    // Capture to Sentry asynchronously (non-blocking)
    // @ts-ignore — @sentry/nextjs is an optional peer dependency
    void import("@sentry/nextjs")
      .then((Sentry) => {
        const eventId = Sentry.captureException(error, {
          extra: { componentStack: errorInfo.componentStack },
        });
        this.setState({ eventId });
      })
      .catch(() => {
        // Sentry not installed — silent no-op
      });
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, eventId: null });
    this.props.router.refresh();
  };

  render(): React.ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    if (this.props.fallback) {
      return this.props.fallback;
    }

    return (
      <div
        role="alert"
        style={{
          padding: "2rem",
          textAlign: "center",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <h2
          style={{
            fontSize: "1.25rem",
            fontWeight: 600,
            marginBottom: "0.5rem",
          }}
        >
          Something went wrong
        </h2>
        <p
          style={{
            color: "#666",
            marginBottom: "1.5rem",
            fontSize: "0.9rem",
          }}
        >
          An unexpected error occurred. You can try again or return to the home
          page.
        </p>
        <div
          style={{ display: "flex", gap: "1rem", justifyContent: "center" }}
        >
          <button
            onClick={this.handleRetry}
            style={{
              padding: "0.5rem 1.25rem",
              backgroundColor: "#2563eb",
              color: "white",
              border: "none",
              borderRadius: "0.375rem",
              cursor: "pointer",
              fontSize: "0.875rem",
              fontWeight: 600,
            }}
          >
            Try Again
          </button>
          <a
            href="/"
            style={{
              padding: "0.5rem 1.25rem",
              border: "1px solid #d1d5db",
              borderRadius: "0.375rem",
              textDecoration: "none",
              color: "#374151",
              fontSize: "0.875rem",
              fontWeight: 600,
            }}
          >
            Go Home
          </a>
        </div>
      </div>
    );
  }
}

/**
 * SentryErrorBoundary — wraps children with error capture and fallback UI.
 * Uses a functional component wrapper to access Next.js router.
 */
export function SentryErrorBoundary({
  children,
  fallback,
}: SentryErrorBoundaryProps): React.ReactNode {
  const router = useRouter();
  return (
    <ErrorBoundaryInner router={router} fallback={fallback}>
      {children}
    </ErrorBoundaryInner>
  );
}
