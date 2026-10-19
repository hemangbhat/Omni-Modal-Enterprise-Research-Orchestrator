import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams()
}));
const claims = { current: { tenant_id: "t1", user_id: "u1", roles: ["admin"] } };
vi.mock("@/lib/use-claims", () => ({ useClaims: () => claims.current }));

import { AnswerMarkdown } from "@/components/answer-markdown";
import { EvidenceCard } from "@/components/evidence-card";
import { WebResearchSettings } from "@/components/web-research-settings";
import type { Citation } from "@/lib/research-api";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const settings = {
  external_research_enabled: false,
  provider_configured: true,
  plan_includes: true,
  monthly_limit: 300,
  used_this_month: 4
};

describe("links in answers", () => {
  it("renders http(s) links safely and leaves other schemes as text", () => {
    render(
      <AnswerMarkdown
        markdown={"1. [Nimbus pricing](https://example.com/nimbus)\n2. [bad](javascript:alert(1))"}
        citationCount={0}
      />
    );
    const link = screen.getByRole("link", { name: "Nimbus pricing" });
    expect((link).getAttribute("href")).toBe("https://example.com/nimbus");
    expect((link).getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
    expect(screen.getByText(/javascript:alert/)).toBeTruthy();
  });

  it("still turns [n] into citation buttons next to links", () => {
    render(<AnswerMarkdown markdown={"Prices fell [1]. See [Survey](https://example.org/s)."} citationCount={1} />);
    expect(screen.getByRole("button", { name: "Show source 1" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Survey" })).toBeTruthy();
  });
});

describe("web evidence", () => {
  const base: Citation = {
    document_id: "https://example.com/nimbus", chunk_id: "web:0", title: "Nimbus pricing", source_type: "web",
    chunk_index: 0, page_number: null, segment_index: null, start_ms: null, end_ms: null,
    snippet: "Storage costs 0.02 dollars.", scope: "external", cited: true, url: "https://example.com/nimbus"
  };

  it("labels web sources and links to them", () => {
    render(<EvidenceCard citation={base} index={1} active={false} />);
    expect(screen.getByText("From the web")).toBeTruthy();
    expect(screen.getByText("example.com")).toBeTruthy();
    expect((screen.getByRole("link", { name: "Nimbus pricing" })).getAttribute("href")).toBe("https://example.com/nimbus");
  });

  it("never links a non-http url", () => {
    render(<EvidenceCard citation={{ ...base, url: "javascript:alert(1)" }} index={1} active={false} />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("web research setting", () => {
  beforeEach(() => {
    claims.current = { tenant_id: "t1", user_id: "u1", roles: ["admin"] };
  });

  it("lets an admin turn it on and shows the allowance", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(settings))
      .mockResolvedValueOnce(json({ ...settings, external_research_enabled: true }));
    vi.stubGlobal("fetch", fetchMock);
    render(<WebResearchSettings />);
    const toggle = await screen.findByRole("switch");
    expect((toggle as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText("4 of 300 searches")).toBeTruthy();
    expect(screen.getByText(/never document text/)).toBeTruthy();
    fireEvent.click(toggle);
    await waitFor(() => expect((screen.getByRole("switch") as HTMLInputElement).checked).toBe(true));
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain("/workspace/research-settings");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ external_research_enabled: true });
  });

  it("is read-only for non-admins and explains a missing plan", async () => {
    claims.current = { tenant_id: "t1", user_id: "u2", roles: ["researcher"] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...settings, plan_includes: false })));
    render(<WebResearchSettings />);
    expect(((await screen.findByRole("switch")) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("Only an admin can change this.")).toBeTruthy();
    expect((screen.getByRole("link", { name: "Compare plans" })).getAttribute("href")).toBe("/pricing");
  });
});

describe("model citation formats", () => {
  it("turns 【n†locator】 into a citation chip while streaming", () => {
    render(<AnswerMarkdown markdown={"Bytecode is compiled 【1†L3-L5】."} citationCount={1} />);
    expect(screen.getByRole("button", { name: "Show source 1" })).toBeTruthy();
    expect(screen.queryByText(/†/)).toBeNull();
  });
});
