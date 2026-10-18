import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ResearchChat } from "@/components/research-chat";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function sse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        for (const chunk of chunks) c.enqueue(encoder.encode(chunk));
        c.close();
      }
    })
  );
}

const citation = {
  document_id: "d1", chunk_id: "c1", title: "Q3 Board Minutes", source_type: "pdf", chunk_index: 0,
  page_number: 4, segment_index: null, start_ms: null, end_ms: null,
  snippet: "Revenue grew 18% on enterprise subscriptions.", scope: "internal", cited: true
};

const session = { id: "s1", kind: "research", title: "Q3 revenue", created_at: 1, updated_at: Date.now() / 1000 };

describe("ResearchChat", () => {
  let queryBodies: Record<string, unknown>[] = [];

  beforeEach(() => {
    queryBodies = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.includes("/research/sessions?kind=research")) return json({ sessions: [session] });
      if (url.endsWith("/research/sessions/s1") && (!init?.method || init.method === "GET")) {
        return json({
          ...session,
          messages: [
            { id: "m1", role: "user", content: "How much did revenue grow?", status: null, citations: [], created_at: 1 },
            {
              id: "m2", role: "assistant", content: "## Answer\n\nRevenue grew 18% [1].\n\n## Sources\n\n1. [1] Q3",
              status: "answered", citations: [citation], created_at: 2
            }
          ]
        });
      }
      if (url.endsWith("/query/stream")) {
        queryBodies.push(JSON.parse(String(init?.body)));
        return sse([
          'event: session\ndata: {"session_id":"s1"}\n\n',
          `event: done\ndata: ${JSON.stringify({
            status: "no_data", session_id: "s1", search_query: "x", citations: [],
            answer_markdown: "## Answer\n\nThe retrieved documents do not contain enough information to answer this."
          })}\n\n`
        ]);
      }
      return json({}, 404);
    });
  });

  it("opens a saved session with its evidence and continues it", async () => {
    render(<ResearchChat />);
    fireEvent.click(await screen.findByRole("button", { name: /^Q3 revenue/ }));

    await waitFor(() => expect(screen.getByText("How much did revenue grow?")).toBeTruthy());
    const evidence = screen.getByRole("complementary", { name: "Evidence" });
    expect(within(evidence).getByText("Revenue grew 18% on enterprise subscriptions.")).toBeTruthy();
    expect(within(evidence).getByText("Page 4")).toBeTruthy();
    // The sources list is shown as cards, not repeated in the answer text.
    expect(screen.queryByText(/## Sources/)).toBeNull();

    // A citation chip highlights its evidence card.
    fireEvent.click(screen.getByRole("button", { name: "Show source 1" }));
    expect(document.getElementById("evidence-1")!.className).toContain("border-primary-fixed-dim");

    fireEvent.change(screen.getByLabelText("Ask a question about your documents"), {
      target: { value: "and headcount?" }
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask/ }));
    await waitFor(() => expect(screen.getByText("Not enough evidence")).toBeTruthy());
    expect(queryBodies[0]).toEqual({ question: "and headcount?", session_id: "s1" });
  });

  it("shows a plain empty state when nothing is saved yet", async () => {
    vi.stubGlobal("fetch", async () => json({ sessions: [] }));
    render(<ResearchChat />);
    expect(await screen.findByText(/Questions you ask are saved here/)).toBeTruthy();
    expect(screen.getByText("Ask anything your documents can answer.")).toBeTruthy();
  });
});
