import { getStoredToken } from "@/lib/auth";

type ServerEnv = {
  appName: string;
  backendBaseUrl: string;
  sentryDsnConfigured: boolean;
};

export function getServerEnv(): ServerEnv {
  return {
    appName:
      process.env.NEXT_PUBLIC_APP_NAME ??
      "Omni-Modal Enterprise Research Orchestrator",
    backendBaseUrl: process.env.BACKEND_BASE_URL ?? "http://localhost:8000",
    sentryDsnConfigured: Boolean(process.env.SENTRY_DSN)
  };
}

type ClientApiConfig = {
  /** Backend base URL reachable from the browser. */
  baseUrl: string;
  /** Optional dev/demo bearer token (issued via scripts/issue_jwt.py). */
  token: string | null;
};

/**
 * Client-side API configuration.
 *
 * - NEXT_PUBLIC_BACKEND_URL: backend origin (default http://localhost:8000)
 * - token: the active session token. Read first from the signed-in session
 *   (localStorage, set on sign-in), falling back to the build-time
 *   NEXT_PUBLIC_API_TOKEN when present. This lets sign-in / sign-out actually
 *   control which credential is sent to the backend.
 */
export function getClientApiConfig(): ClientApiConfig {
  const sessionToken = getStoredToken();
  return {
    baseUrl: process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000",
    token: sessionToken ?? process.env.NEXT_PUBLIC_API_TOKEN ?? null
  };
}
