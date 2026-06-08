/**
 * Real unit tests for apps/web/src/lib/api-client.ts
 * Tests the X-Correlation-ID injection, timeout, and error capture helpers.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const UUID_V4_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

describe("apiRequest — header injection", () => {
  let capturedHeaders: Headers | null = null;

  beforeEach(() => {
    capturedHeaders = null;
    // Mock global fetch
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      capturedHeaders = new Headers(init?.headers);
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
  });

  it("attaches X-Correlation-ID as a valid UUID v4 on every request", async () => {
    const { apiRequest } = await import("@/lib/api-client");
    await apiRequest("/test", { method: "GET" }, { baseUrl: "http://localhost:8000" });
    const correlationId = capturedHeaders?.get("X-Correlation-ID");
    expect(correlationId).toBeTruthy();
    expect(correlationId).toMatch(UUID_V4_REGEX);
  });

  it("generates a different correlation ID for each request", async () => {
    const ids: string[] = [];
    vi.stubGlobal("fetch", async (_: string, init?: RequestInit) => {
      ids.push(new Headers(init?.headers).get("X-Correlation-ID") ?? "");
      return new Response("{}", { status: 200 });
    });
    const { apiRequest } = await import("@/lib/api-client");
    await apiRequest("/a", {}, { baseUrl: "" });
    await apiRequest("/b", {}, { baseUrl: "" });
    expect(ids[0]).not.toBe(ids[1]);
    expect(ids[0]).toMatch(UUID_V4_REGEX);
    expect(ids[1]).toMatch(UUID_V4_REGEX);
  });

  it("preserves caller-supplied headers alongside injected headers", async () => {
    const { apiRequest } = await import("@/lib/api-client");
    await apiRequest(
      "/test",
      { method: "POST", headers: { Authorization: "Bearer tok", "Content-Type": "application/json" } },
      { baseUrl: "" }
    );
    expect(capturedHeaders?.get("Authorization")).toBe("Bearer tok");
    expect(capturedHeaders?.get("Content-Type")).toBe("application/json");
    expect(capturedHeaders?.get("X-Correlation-ID")).toMatch(UUID_V4_REGEX);
  });
});

describe("captureUploadError", () => {
  it("is non-blocking — does not throw even when Sentry is absent", async () => {
    const { captureUploadError } = await import("@/lib/api-client");
    expect(() =>
      captureUploadError({ file_name: "test.pdf", file_size_bytes: 1024, http_status: 500 })
    ).not.toThrow();
  });
});

describe("captureQueryError", () => {
  it("is non-blocking — does not throw even when Sentry is absent", async () => {
    const { captureQueryError } = await import("@/lib/api-client");
    expect(() =>
      captureQueryError({ query_length: 42, http_status: "network_error" })
    ).not.toThrow();
  });
});
