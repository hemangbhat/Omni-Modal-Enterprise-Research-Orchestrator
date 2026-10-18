import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/projects",
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  useParams: () => ({ id: "p1" })
}));
vi.mock("@/components/top-bar", () => ({ TopBar: () => null }));
vi.mock("@/lib/use-claims", () => ({
  useClaims: () => ({ tenant_id: "t1", user_id: "u1", roles: ["researcher"] })
}));

import ProjectsPage from "@/app/projects/page";
import ProjectPage from "@/app/projects/[id]/page";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const project = {
  id: "p1", name: "Q3 review", description: "Board prep", created_by: "u1",
  created_at: 1, updated_at: Date.now() / 1000, archived_at: null, archived: false
};
const citation = {
  document_id: "d1", chunk_id: "c1", title: "Board Minutes", source_type: "pdf", chunk_index: 0,
  page_number: 2, segment_index: null, start_ms: null, end_ms: null,
  snippet: "The board approved a budget of 2.4 million dollars.", scope: "internal", cited: true
};
const detail = {
  project,
  documents: [{ id: "d1", document_id: "d1", title: "Board Minutes", status: "ready" }],
  items: [{
    id: "i1", project_id: "p1", kind: "finding", title: "What did the board approve?",
    body: "A budget of 2.4 million [1].", citations: [citation], source_session_id: null,
    created_by: "u1", created_at: 1, updated_at: 1
  }],
  reports: [],
  sessions: [],
  insights: { decision: [{ value: "approved a budget", document_id: "d1", document_title: "Board Minutes", confidence: 0.8 }],
              risk: [], action_item: [], owner: [], deadline: [] },
  activity: [{ id: 2, actor: "u1", action: "finding_added", detail: { title: "What did the board approve?" }, created_at: 2 }]
};

describe("Projects list", () => {
  beforeEach(() => push.mockReset());

  it("shows real counts and creates a project", async () => {
    let created: unknown = null;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.endsWith("/projects") && init?.method === "POST") {
        created = JSON.parse(String(init.body));
        return json({ ...project, id: "p2" }, 201);
      }
      return json({ projects: [{ ...project, counts: { documents: 3, findings: 2, notes: 0, reports: 1 } }] });
    });
    render(<ProjectsPage />);
    const card = await screen.findByRole("link", { name: /Q3 review/ });
    expect(within(card).getByText("3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /New project/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Pricing study" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/p2"));
    expect(created).toEqual({ name: "Pricing study", description: "" });
  });
});

describe("Project page", () => {
  it("shows findings with their sources, insights and a generated report", async () => {
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.endsWith("/projects/p1/reports") && init?.method === "POST") {
        return json({ id: "r1", project_id: "p1", source: "project", source_session_id: null, title: "Q3 review report",
                      markdown: "# Q3 review\\n\\n## Findings\\n\\nA budget of 2.4 million [1].", created_by: "u1", created_at: 3 });
      }
      if (url.includes("/documents")) return json({ documents: [] });
      return json(detail);
    });
    render(<ProjectPage />);
    expect(await screen.findByRole("heading", { name: "Q3 review" })).toBeTruthy();
    expect(screen.getByText("approved a budget")).toBeTruthy();
    expect(screen.getByText(/Saved finding/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Show 1 source/ }));
    expect(screen.getByText("The board approved a budget of 2.4 million dollars.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Generate report/ }));
    const dialog = await screen.findByRole("dialog", { name: "Q3 review report" });
    expect(within(dialog).getByRole("button", { name: /Download .md/ })).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
