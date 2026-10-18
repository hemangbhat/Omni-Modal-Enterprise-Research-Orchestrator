import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a real API (in memory, no keys, no database) and a
 * production build of the frontend, on ports that do not clash with dev servers.
 *
 * Locally this uses the installed Chrome; CI sets PLAYWRIGHT_CHROMIUM=1 and
 * installs Playwright's own Chromium.
 */
const API_PORT = 8100;
const WEB_PORT = 3100;
const API = `http://localhost:${API_PORT}`;
const WEB = `http://localhost:${WEB_PORT}`;

const python = process.env.PYTHON ?? "python";
const apiEnv = {
  OMERO_DISABLE_DOTENV: "1",
  JWT_SECRET: "e2e-test-secret",
  PORT: String(API_PORT),
  HOST: "127.0.0.1",
  CORS_ALLOW_ORIGINS: WEB,
  DATABASE_URL: "",
  REDIS_URL: "",
  GROQ_API_KEY: "",
  GEMINI_API_KEY: "",
  S3_BUCKET: "",
  SENTRY_DSN: "",
  STRIPE_SECRET_KEY: ""
};

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: WEB,
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  projects: [
    {
      name: "chromium",
      use: process.env.PLAYWRIGHT_CHROMIUM
        ? { ...devices["Desktop Chrome"] }
        : { ...devices["Desktop Chrome"], channel: "chrome" }
    }
  ],
  webServer: [
    {
      command: `${python} -m omni_modal.api`,
      cwd: "../../services/api/src",
      url: `${API}/health`,
      env: apiEnv,
      reuseExistingServer: false,
      timeout: 120_000
    },
    {
      command: `npx next build && npx next start -p ${WEB_PORT}`,
      url: WEB,
      env: {
        NEXT_PUBLIC_BACKEND_URL: API,
        NEXT_PUBLIC_API_TOKEN: "",
        NEXT_PUBLIC_SENTRY_DSN: "",
        SENTRY_DSN: ""
      },
      reuseExistingServer: false,
      timeout: 300_000
    }
  ]
});
