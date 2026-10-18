import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AnswerMarkdown, parseBlocks } from "@/components/answer-markdown";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/documents",
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search)
}));

import { AssistantPanel } from "@/components/assistant-panel";

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c));
        controller.close();
      }
    }),
    { status: 200 }
  );
}

describe("AnswerMarkdown", () => {
  it("groups lists and paragraphs", () => {
    expect(parseBlocks("Intro line\nsecond\n\n- a\n- b\n1. c").map((b) => b.kind)).toEqual(["p", "ul", "ol"]);
  });

  it("turns valid citation markers into buttons and leaves invalid ones as text", () => {
    const onCite = vi.fn();
    render(<AnswerMarkdown markdown="Revenue grew [1, 2] but see [9]." citationCount={2} onCite={onCite} />);
    fireEvent.click(screen.getByRole("button", { name: "Show source 2" }));
    expect(onCite).toHaveBeenCalledWith(2);
    expect(screen.queryByRole("button", { name: "Show source 9" })).toBeNull();
    expect(screen.getByText(/\[9\]/)).toBeTruthy();
  });

  it("renders bold and italic", () => {
    const { container } = render(<AnswerMarkdown markdown="A **bold** and *italic* word." citationCount={0} />);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("italic");
  });

  it("renders text, never HTML", () => {
    const { container } = render(<AnswerMarkdown markdown="<img src=x onerror=alert(1)>" citationCount={0} />);
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("AssistantPanel", () => {
  let sentBody: Record<string, unknown> = {};

  beforeEach(() => {
    push.mockReset();
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      sentBody = JSON.parse(String(init?.body));
      return sseResponse([
        'event: session\ndata: {"session_id":"a1"}\n\n',
        'event: tool\ndata: {"name":"navigate","label":"Suggested a page","ok":true}\n\n',
        'event: delta\ndata: {"delta":"The Team page manages members."}\n\n',
        'event: done\ndata: {"answer_markdown":"The Team page manages members.","citations":[],"tools":[],"actions":[{"label":"Open Team","href":"/team"}],"model":"none","session_id":"a1"}\n\n'
      ]);
    });
  });

  it("sends the current page, shows what it checked, and offers a link", async () => {
    const onClose = vi.fn();
    render(<AssistantPanel open onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("Message the assistant"), { target: { value: "take me to the team page" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByText("The Team page manages members.")).toBeTruthy());
    expect(sentBody).toEqual({ message: "take me to the team page", page: "/documents" });
    expect(screen.getByText("Suggested a page")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Open Team/ }));
    expect(push).toHaveBeenCalledWith("/team");
    expect(onClose).toHaveBeenCalled();
  });

  it("continues the same conversation on the next message", async () => {
    render(<AssistantPanel open onClose={() => {}} />);
    const input = screen.getByLabelText("Message the assistant");
    fireEvent.change(input, { target: { value: "first" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByText("The Team page manages members.")).toBeTruthy());
    fireEvent.change(input, { target: { value: "second" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sentBody.session_id).toBe("a1"));
  });

  it("offers suggestions for the current page", () => {
    render(<AssistantPanel open onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "Which of my documents are still processing?" })).toBeTruthy();
  });
});
