/**
 * Real unit tests for apps/web/src/lib/env.ts
 * Covers getClientApiConfig and getServerEnv defaults + overrides.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// We test the functions directly by mocking process.env
describe("getClientApiConfig", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    // Reset process.env before each test
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
    vi.resetModules();
  });

  it("returns default baseUrl when NEXT_PUBLIC_BACKEND_URL is not set", async () => {
    delete process.env.NEXT_PUBLIC_BACKEND_URL;
    delete process.env.NEXT_PUBLIC_API_TOKEN;
    const { getClientApiConfig } = await import("@/lib/env");
    const config = getClientApiConfig();
    expect(config.baseUrl).toBe("http://localhost:8000");
  });

  it("returns token as null when NEXT_PUBLIC_API_TOKEN is not set", async () => {
    delete process.env.NEXT_PUBLIC_API_TOKEN;
    const { getClientApiConfig } = await import("@/lib/env");
    const config = getClientApiConfig();
    expect(config.token).toBeNull();
  });

  it("returns provided NEXT_PUBLIC_BACKEND_URL", async () => {
    process.env.NEXT_PUBLIC_BACKEND_URL = "http://api.example.com:9000";
    const { getClientApiConfig } = await import("@/lib/env");
    const config = getClientApiConfig();
    expect(config.baseUrl).toBe("http://api.example.com:9000");
  });

  it("returns provided NEXT_PUBLIC_API_TOKEN", async () => {
    process.env.NEXT_PUBLIC_API_TOKEN = "test-bearer-token";
    const { getClientApiConfig } = await import("@/lib/env");
    const config = getClientApiConfig();
    expect(config.token).toBe("test-bearer-token");
  });
});

describe("getServerEnv", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
    vi.resetModules();
  });

  it("returns default appName when not configured", async () => {
    delete process.env.NEXT_PUBLIC_APP_NAME;
    const { getServerEnv } = await import("@/lib/env");
    const env = getServerEnv();
    expect(env.appName).toBe("Omni-Modal Enterprise Research Orchestrator");
  });

  it("returns sentryDsnConfigured=true when SENTRY_DSN is set", async () => {
    process.env.SENTRY_DSN = "https://example@sentry.io/123";
    const { getServerEnv } = await import("@/lib/env");
    const env = getServerEnv();
    expect(env.sentryDsnConfigured).toBe(true);
  });

  it("returns sentryDsnConfigured=false when SENTRY_DSN is empty", async () => {
    process.env.SENTRY_DSN = "";
    const { getServerEnv } = await import("@/lib/env");
    const env = getServerEnv();
    expect(env.sentryDsnConfigured).toBe(false);
  });
});
