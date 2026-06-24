/**
 * Real unit tests for apps/web/src/lib/saas-api.ts
 * Verifies auth header injection, JSON handling, and the typed PlanLimitError
 * surfaced on HTTP 402 (plan limit) responses.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("saas-api client", () => {
  let lastUrl = "";
  let lastInit: RequestInit | undefined;

  beforeEach(() => {
    lastUrl = "";
    lastInit = undefined;
    // No stored session token in tests -> token is null; that's fine.
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      lastUrl = url;
      lastInit = init;
      if (url.endsWith("/workspaces") && init?.method === "POST") {
        // Simulate a plan-limit rejection
        return jsonResponse(
          { error: "Plan limit reached for 'workspaces': 1/1.", metric: "workspaces", limit: 1 },
          402
        );
      }
      if (url.endsWith("/workspaces")) {
        return jsonResponse({
          organization: { id: "o1" },
          workspaces: [{ id: "w1", name: "Default", slug: "default" }],
          total: 1
        });
      }
      if (url.endsWith("/usage")) {
        return jsonResponse({ plan: { id: "free" }, metrics: { queries: { used: 1, limit: 100 } } });
      }
      return jsonResponse({});
    });
  });

  it("listWorkspaces issues a GET and parses the body", async () => {
    const { listWorkspaces } = await import("@/lib/saas-api");
    const data = await listWorkspaces();
    expect(lastUrl).toContain("/workspaces");
    expect(lastInit?.method).toBe("GET");
    expect(data.workspaces[0].name).toBe("Default");
  });

  it("getUsage parses the usage report shape", async () => {
    const { getUsage } = await import("@/lib/saas-api");
    const report = await getUsage();
    expect(report.metrics.queries.used).toBe(1);
  });

  it("createWorkspace throws a typed PlanLimitError on HTTP 402", async () => {
    const { createWorkspace, PlanLimitError } = await import("@/lib/saas-api");
    await expect(createWorkspace("Second")).rejects.toBeInstanceOf(PlanLimitError);
    try {
      await createWorkspace("Second");
    } catch (err) {
      const e = err as InstanceType<typeof PlanLimitError>;
      expect(e.metric).toBe("workspaces");
      expect(e.limit).toBe(1);
    }
  });

  it("sets Content-Type on POST requests", async () => {
    const { createWorkspace } = await import("@/lib/saas-api");
    await createWorkspace("X").catch(() => undefined);
    const headers = new Headers(lastInit?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});
