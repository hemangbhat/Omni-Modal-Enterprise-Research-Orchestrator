import { describe, it, expect, vi, beforeEach } from "vitest";
import { parseSseBlock, readSse } from "@/lib/sse";
import {
  answerBody,
  citationLocation,
  streamResearch,
  type Citation,
  type ResearchDone
} from "@/lib/research-api";

function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    }
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

const citation: Citation = {
  document_id: "d1", chunk_id: "c1", title: "Board Minutes", source_type: "pdf", chunk_index: 0,
  page_number: null, segment_index: null, start_ms: null, end_ms: null, snippet: "", scope: "internal", cited: true
};

describe("sse", () => {
  it("parses one event block", () => {
    expect(parseSseBlock('event: delta\ndata: {"delta":"hi"}')).toEqual({ event: "delta", data: { delta: "hi" } });
    expect(parseSseBlock("event: ping")).toBeNull();
    expect(parseSseBlock("data: not json")).toBeNull();
  });

  it("reassembles events split across network chunks", async () => {
    const events: [string, unknown][] = [];
    await readSse(
      sseResponse(['event: delta\ndata: {"del', 'ta":"a"}\n\nevent: done\r\ndata: {"ok":true}\r\n\r\n']).body!,
      (e, d) => events.push([e, d])
    );
    expect(events).toEqual([["delta", { delta: "a" }], ["done", { ok: true }]]);
  });

  it("joins a CR/LF pair split across two reads", async () => {
    const events: string[] = [];
    await readSse(
      sseResponse(['event: delta\r\ndata: {"delta":"a"}\r\n\r', '\nevent: done\r\ndata: {}\r\n\r\n']).body!,
      (e) => events.push(e)
    );
    expect(events).toEqual(["delta", "done"]);
  });
});

describe("streamResearch", () => {
  let lastBody: Record<string, unknown> = {};

  beforeEach(() => {
    lastBody = {};
  });

  it("forwards session, deltas and the final answer, and continues a session", async () => {
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      lastBody = JSON.parse(String(init?.body));
      return sseResponse([
        'event: session\ndata: {"session_id":"s1"}\n\n',
        'event: delta\ndata: {"delta":"## Answer\\n\\nRevenue"}\n\n',
        'event: done\ndata: {"status":"answered","answer_markdown":"## Answer\\n\\nRevenue [1]","citations":[],"session_id":"s1","search_query":null}\n\n'
      ]);
    });
    const seen: string[] = [];
    let done: ResearchDone | null = null;
    await streamResearch("q", "s1", {
      onSession: (id) => seen.push(`session:${id}`),
      onDelta: (d) => seen.push(d),
      onDone: (d) => (done = d)
    });
    expect(lastBody).toEqual({ question: "q", session_id: "s1" });
    expect(seen).toEqual(["session:s1", "## Answer\n\nRevenue"]);
    expect(done!.status).toBe("answered");
  });

  it("explains a plan limit in plain words", async () => {
    vi.stubGlobal("fetch", async () =>
      new Response(JSON.stringify({ detail: { error: "Plan limit reached", metric: "queries" } }), { status: 402 })
    );
    await expect(streamResearch("q", null, { onDone: () => {} })).rejects.toThrow(/Upgrade on the Billing page/);
  });

  it("fails clearly when the stream ends without an answer", async () => {
    vi.stubGlobal("fetch", async () => sseResponse(['event: session\ndata: {"session_id":"s1"}\n\n']));
    await expect(streamResearch("q", null, { onDone: () => {} })).rejects.toThrow(/closed before the answer finished/);
  });

  it("surfaces a server error event", async () => {
    vi.stubGlobal("fetch", async () => sseResponse(['event: error\ndata: {"error":"Query failed."}\n\n']));
    await expect(streamResearch("q", null, { onDone: () => {} })).rejects.toThrow("Query failed.");
  });
});

describe("citation helpers", () => {
  it("formats page, audio range and segment", () => {
    expect(citationLocation({ ...citation, page_number: 4 })).toBe("Page 4");
    expect(citationLocation({ ...citation, start_ms: 65_000, end_ms: 3_725_000 })).toBe("1:05–1:02:05");
    expect(citationLocation({ ...citation, segment_index: 2 })).toBe("Segment 2");
    expect(citationLocation(citation)).toBeNull();
  });

  it("strips the answer heading and the sources list", () => {
    expect(answerBody("## Answer\n\nRevenue grew [1].\n\n## Sources\n\n1. [1] Q3")).toBe("Revenue grew [1].");
  });
});
