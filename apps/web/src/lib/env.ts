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
