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
 * Client-side API configuration, read from NEXT_PUBLIC_* env vars so it is
 * available in the browser.
 *
 * - NEXT_PUBLIC_BACKEND_URL: backend origin (default http://localhost:8000)
 * - NEXT_PUBLIC_API_TOKEN: optional bearer token for local demos. In a real
 *   deployment the token would come from an authenticated session, not an env
 *   var — this is a development convenience only.
 */
export function getClientApiConfig(): ClientApiConfig {
  return {
    baseUrl: process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000",
    token: process.env.NEXT_PUBLIC_API_TOKEN ?? null
  };
}
