import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  useParams: () => ({})
}));
vi.mock("@/lib/use-claims", () => ({ useClaims: () => ({ tenant_id: "t1", user_id: "u1", roles: ["admin"] }) }));
vi.mock("@/components/auth-context", () => ({
  useAuth: () => ({ token: null, isReady: true, isAuthenticated: true, signIn: vi.fn(), signOut: vi.fn() })
}));

import HomePage from "@/app/page";
import DocumentsPage from "@/app/documents/page";
import ArchivesPage from "@/app/archives/page";
import PricingPage from "@/app/pricing/page";
import { BillingHistory } from "@/components/billing-history";
import { FailedJobs } from "@/components/failed-jobs";
import { TopBar } from "@/components/top-bar";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const usage = {
  plan: { id: "free", name: "Free" },
  metrics: { queries: { used: 3, limit: 100, unlimited: false, percent: 3 }, uploads: { used: 1, limit: 25, unlimited: false, percent: 4 } }
};

function doc(id: string, title: string, extra: Record<string, unknown> = {}) {
  return { id, document_id: id, title, file_name: `${title}.pdf`, source_kind: "pdf", size_bytes: 2048,
           status: "ready", chunk_count: 2, created_at: 1_700_000_000, error_message: null, ...extra };
}

describe("Dashboard", () => {
  it("shows real counts and the getting-started steps for a new workspace", async () => {
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.includes("/dashboard")) {
        return json({ documents: { ready: 0, processing: 0, uploaded: 0, failed: 0, archived: 0, total: 0 },
                      projects: 0, recent_documents: [], recent_sessions: [], recent_activity: [], usage,
                      progress: { uploaded: true, asked: false, project: false, report: false } });
      }
      return json({});
    });
    render(<HomePage />);
    expect(await screen.findByText("Getting started")).toBeTruthy();
    expect(screen.getByText("1 of 4 done. Next: ask a question.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Upload a document (done)" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByText("Getting started")).toBeNull();
    expect(window.localStorage.getItem("omero.firstRun.dismissed.u1")).toBe("1");
    expect(screen.getByText("0 documents and 0 active projects.")).toBeTruthy();
    expect(screen.getByText("3 of 100")).toBeTruthy();
  });
});

describe("Documents page", () => {
  let urls: string[] = [];
  beforeEach(() => {
    urls = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      urls.push(url);
      if (url.includes("/archive") && init?.method === "POST") return json({ document: doc("d1", "Minutes") });
      if (url.includes("/documents?")) {
        const page = new URL(url).searchParams.get("page");
        return json({ documents: page === "2" ? [doc("d26", "Later")] : [doc("d1", "Minutes")], total: 26 });
      }
      return json({ entities: [] });
    });
  });

  it("sends filters to the server, pages, and archives", async () => {
    render(<DocumentsPage />);
    expect(await screen.findByText("Minutes")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
    await waitFor(() => expect(urls.some((u) => u.includes("status=failed"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "" } });

    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(await screen.findByText("Later")).toBeTruthy();
    expect(urls.some((u) => u.includes("page=2"))).toBe(true);
  });
});

describe("Archives page", () => {
  it("lists archived documents and restores one", async () => {
    let restored = false;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.includes("/restore") && init?.method === "POST") {
        restored = true;
        return json({ document: doc("d1", "Old plan") });
      }
      return json({ documents: restored ? [] : [doc("d1", "Old plan", { archived_at: 1_700_000_100 })], total: restored ? 0 : 1 });
    });
    render(<ArchivesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    await waitFor(() => expect(screen.getByText("Nothing is archived.")).toBeTruthy());
  });
});

describe("Top bar search", () => {
  it("shows results from documents, projects and research with the stated scope", async () => {
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.includes("/search?q=")) {
        return json({ documents: [{ id: "d1", title: "Pelican budget", status: "ready" }],
                      projects: [{ id: "p1", name: "Pelican launch" }], sessions: [],
                      scope: "Document titles and file names, project names and descriptions, and your research session titles." });
      }
      return json({ workspaces: [], notifications: [], unread: 0 });
    });
    render(<TopBar />);
    fireEvent.change(screen.getByLabelText("Search workspace"), { target: { value: "pelican" } });
    expect(await screen.findByRole("button", { name: "Pelican launch" })).toBeTruthy();
    expect(screen.getByText(/Searches document titles/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pelican launch" }));
    expect(push).toHaveBeenCalledWith("/projects/p1");
  });
});

describe("Pricing", () => {
  it("lists only enforced limits, never unbuilt features", async () => {
    vi.stubGlobal("fetch", async () =>
      json({ plans: [{ id: "free", name: "Free", price_usd_month: 0, max_workspaces: 1, max_members: 3,
                       monthly_query_limit: 100, monthly_upload_limit: 25, storage_mb: 100, features: ["sso", "api_access"] }] })
    );
    render(<PricingPage />);
    expect(await screen.findByText("100 a month")).toBeTruthy();
    expect(screen.queryByText(/SSO/)).toBeNull();
    expect(screen.queryByText(/storage/i)).toBeNull();
  });
});

describe("Billing history and failed jobs", () => {
  it("says when billing is not connected", async () => {
    vi.stubGlobal("fetch", async () => json({ available: false, invoices: [] }));
    render(<BillingHistory />);
    expect(await screen.findByText(/once Stripe billing is connected/)).toBeTruthy();
  });

  it("retries a failed document", async () => {
    let retried = "";
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        retried = url;
        return json({ job: { stage: "queued" } });
      }
      return json({ failures: [{ document: doc("d9", "Broken scan", { status: "failed", error_message: "No text was extracted." }),
                                  job: { stage: "failed", attempts: 1, error_message: null } }] });
    });
    render(<FailedJobs />);
    expect(await screen.findByText("No text was extracted.")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    });
    expect(retried).toContain("/admin/ingestion/failures/d9/retry");
    expect(screen.getByText("Retry started")).toBeTruthy();
  });
});
