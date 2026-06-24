"use client";

import { useEffect, useRef, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { apiRequest, captureQueryError } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

type SourceCitation = {
  title: string;
  source_type: string;
  chunk_index: number;
};

type HistoryEntry = { id: string; question: string; time: string };

const HISTORY_KEY = "omni_research_history";
const HISTORY_LIMIT = 50;

function loadHistory(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is HistoryEntry =>
        !!e &&
        typeof (e as HistoryEntry).id === "string" &&
        typeof (e as HistoryEntry).question === "string"
    );
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_LIMIT)));
  } catch {
    /* ignore quota / unavailable storage */
  }
}

export function ResearchChat() {
  const [prompt, setPrompt] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [answer, setAnswer] = useState("");
  const [activeQuestion, setActiveQuestion] = useState<string | null>(null);
  const [citations, setCitations] = useState<SourceCitation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const answerRef = useRef("");

  // Restore persisted query history on mount so prior queries are visible
  // immediately (not only after sending a new one).
  useEffect(() => {
    setHistory(loadHistory());
  }, []);

  async function submitPrompt(explicitQuestion?: string) {
    const trimmed = (explicitQuestion ?? prompt).trim();
    if (!trimmed || isStreaming) return;

    // Reuse an existing history entry when re-running a past query; otherwise
    // create a new one. `id`/`time` are computed synchronously so they're
    // available immediately; the updater persists the reordered list.
    const existing = history.find((entry) => entry.question === trimmed);
    const id = existing ? existing.id : `q-${Date.now()}`;
    const time = existing
      ? existing.time
      : new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    setHistory((current) => {
      const without = current.filter((entry) => entry.id !== id);
      const next = [{ id, question: trimmed, time }, ...without];
      saveHistory(next);
      return next;
    });
    setActiveId(id);
    setActiveQuestion(trimmed);
    setPrompt("");
    setError(null);
    setCitations([]);
    setAnswer("");
    answerRef.current = "";
    setIsStreaming(true);

    const { baseUrl, token } = getClientApiConfig();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "text/event-stream"
    };
    if (token) headers.Authorization = `Bearer ${token}`;

    try {
      const response = await apiRequest(
        "/query/stream",
        { method: "POST", headers, body: JSON.stringify({ question: trimmed }) },
        { baseUrl, timeout: 60_000 }
      );
      if (!response.ok || !response.body) {
        captureQueryError({ query_length: trimmed.length, http_status: response.status });
        setError(`Request failed (HTTP ${response.status}).`);
        setIsStreaming(false);
        return;
      }
      await consumeSse(response.body, {
        onDelta: (delta) => {
          answerRef.current += delta;
          setAnswer(answerRef.current);
        },
        onDone: (payload) => {
          const cites = Array.isArray(payload?.citations)
            ? (payload.citations as SourceCitation[])
            : [];
          setCitations(cites);
        }
      });
    } catch (err) {
      captureQueryError({ query_length: trimmed.length, http_status: "network_error" });
      setError(err instanceof Error ? err.message : "Unexpected error contacting backend.");
    } finally {
      setIsStreaming(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh)] max-w-max_width md:h-screen">
      {/* Column 1: Query History */}
      <aside className="relative z-10 hidden w-[300px] flex-col border-r border-outline-variant/10 bg-surface-container-lowest/30 shadow-[4px_0_24px_rgba(0,0,0,0.1)] backdrop-blur-md lg:flex">
        <div className="flex items-center justify-between px-xl py-lg">
          <h2 className="font-mono-sm text-[11px] font-medium uppercase tracking-[0.15em] text-on-surface-variant">
            Recent Queries
          </h2>
          {history.length > 0 ? (
            <button
              type="button"
              onClick={() => {
                setHistory([]);
                saveHistory([]);
              }}
              className="font-mono-sm text-[10px] uppercase tracking-wider text-on-surface-variant/60 transition-colors hover:text-error"
            >
              Clear
            </button>
          ) : null}
        </div>
        <div className="flex flex-1 flex-col gap-md overflow-y-auto px-lg pb-lg">
          {history.length === 0 ? (
            <p className="font-body-md text-body-md text-on-surface-variant/60">
              Your research queries will appear here.
            </p>
          ) : (
            history.map((entry) => {
              const active = entry.id === activeId;
              return (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => {
                    setActiveId(entry.id);
                    setActiveQuestion(entry.question);
                    void submitPrompt(entry.question);
                  }}
                  className={
                    active
                      ? "flex w-full flex-col gap-md rounded-2xl border border-outline-variant/30 bg-gradient-to-br from-surface-container-high to-surface-container p-lg text-left text-primary shadow-lg transition-all"
                      : "group flex w-full flex-col gap-md rounded-2xl border border-transparent bg-transparent p-lg text-left transition-all hover:border-outline-variant/10 hover:bg-surface-container-low/50"
                  }
                >
                  <span
                    className={`font-body-md text-body-md leading-tight ${
                      active
                        ? "font-medium"
                        : "text-on-surface-variant group-hover:text-on-surface"
                    }`}
                  >
                    {entry.question}
                  </span>
                  <span
                    className={`font-mono-sm text-[11px] ${
                      active ? "text-primary/70" : "text-on-surface-variant/60"
                    }`}
                  >
                    Today, {entry.time}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </aside>

      {/* Column 2: Answer panel */}
      <section className="relative z-0 flex min-w-0 flex-1 flex-col bg-surface">
        <div className="flex-1 overflow-y-auto px-lg py-xl pb-[160px] md:px-[80px] md:py-[60px]">
          <div className="flex max-w-3xl flex-col gap-[48px]">
            <div className="flex flex-col gap-lg">
              <h2 className="font-headline-lg text-headline-lg leading-tight tracking-tight text-on-surface">
                {activeQuestion ?? "Ask a research question to begin"}
              </h2>
              {activeQuestion ? (
                <div className="flex items-center gap-lg">
                  <span className="flex items-center gap-sm rounded-lg border border-primary-container/20 bg-primary-container/5 px-md py-sm font-mono-sm text-mono-sm text-primary-fixed-dim shadow-sm backdrop-blur-sm">
                    <MaterialIcon name="auto_awesome" size={16} className="text-primary-fixed" />
                    AI Synthesis
                  </span>
                  <span className="flex items-center gap-sm font-mono-sm text-mono-sm text-on-surface-variant">
                    <div className="h-2 w-2 rounded-full bg-primary-fixed" />
                    {isStreaming ? "Synthesizing..." : "Confidence: High"}
                  </span>
                </div>
              ) : null}
            </div>

            {error ? (
              <div className="rounded-2xl border border-error/30 bg-error-container/20 px-lg py-md font-body-md text-body-md text-error">
                {error}
              </div>
            ) : null}

            <article className="flex flex-col gap-lg font-body-lg text-[17px] leading-[1.8] text-on-surface/90">
              {answer.trim().length > 0 ? (
                answer.split("\n").map((line, index) => <AnswerLine key={index} line={line} />)
              ) : (
                <p className="font-light text-[19px] leading-[1.7] text-on-surface-variant">
                  {isStreaming
                    ? "Synthesizing an evidence-backed answer from your ingested corpus..."
                    : "Responses stream from the backend retrieval and synthesis pipeline, with cited evidence shown on the right."}
                </p>
              )}
              {isStreaming ? (
                <span className="inline-block h-4 w-2 animate-pulse bg-primary-fixed align-middle" />
              ) : null}
            </article>
          </div>
        </div>

        {/* Fixed input */}
        <div className="pointer-events-none absolute bottom-0 left-0 w-full bg-gradient-to-t from-surface via-surface/95 to-transparent p-lg pt-[80px] md:p-[40px]">
          <div className="pointer-events-auto max-w-3xl">
            <div className="relative overflow-hidden rounded-2xl border border-outline-variant/20 bg-surface-container-low/80 shadow-[0_8px_32px_rgba(0,0,0,0.3)] backdrop-blur-2xl transition-all duration-300 focus-within:border-primary-fixed/40 focus-within:ring-2 focus-within:ring-primary-fixed/20">
              <textarea
                rows={1}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitPrompt();
                  }
                }}
                placeholder="Ask a follow-up question or request deeper analysis..."
                className="block w-full resize-none border-none bg-transparent p-lg font-body-lg text-[17px] text-on-surface placeholder-on-surface-variant/40 focus:ring-0 md:p-xl"
              />
              <div className="flex items-center justify-between px-lg pb-lg pt-sm">
                <div className="flex gap-md">
                  <span className="rounded-lg p-sm text-on-surface-variant/70">
                    <MaterialIcon name="attach_file" size={22} />
                  </span>
                  <span className="rounded-lg p-sm text-on-surface-variant/70">
                    <MaterialIcon name="mic" size={22} />
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => void submitPrompt()}
                  disabled={!prompt.trim() || isStreaming}
                  className="flex items-center gap-sm rounded-xl bg-gradient-to-r from-primary-container to-primary px-xl py-md font-label-md text-label-md font-semibold tracking-wide text-on-primary-container transition-all duration-300 hover:-translate-y-0.5 hover:shadow-[0_0_16px_rgba(0,218,243,0.3)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isStreaming ? "Streaming" : "Send"}
                  <MaterialIcon name="send" size={18} />
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Column 3: Sources */}
      <aside className="relative z-10 hidden w-[360px] flex-col border-l border-outline-variant/10 bg-surface-container-lowest/30 shadow-[-4px_0_24px_rgba(0,0,0,0.1)] backdrop-blur-md xl:flex">
        <div className="flex items-center justify-between px-xl py-lg">
          <h2 className="font-mono-sm text-[11px] font-medium uppercase tracking-[0.15em] text-on-surface-variant">
            Evidence &amp; Sources
          </h2>
          <MaterialIcon name="tune" size={20} className="text-on-surface-variant" />
        </div>
        <div className="flex flex-1 flex-col gap-lg overflow-y-auto px-lg pb-xl">
          {citations.length === 0 ? (
            <p className="font-body-md text-body-md text-on-surface-variant/60">
              Cited sources from retrieved documents will appear here after a query.
            </p>
          ) : (
            citations.map((cite, index) => (
              <div
                key={`${cite.title}-${index}`}
                className="group relative cursor-pointer overflow-hidden rounded-2xl border border-outline-variant/20 bg-gradient-to-br from-surface-container-low to-surface/40 p-lg shadow-lg transition-all duration-300 hover:border-primary-fixed/30"
              >
                <div className="absolute -right-10 -top-10 h-32 w-32 rounded-full bg-primary-fixed/5 blur-2xl transition-all group-hover:bg-primary-fixed/10" />
                <div className="relative mb-lg flex items-center gap-md">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-outline-variant/20 bg-surface-container-high font-mono-sm text-primary-fixed shadow-sm">
                    {index + 1}
                  </span>
                  <span className="font-mono-sm text-[11px] uppercase tracking-wider text-on-surface-variant">
                    {cite.source_type} · chunk {cite.chunk_index}
                  </span>
                </div>
                <h4 className="relative mb-sm font-body-md text-body-md font-medium leading-tight text-on-surface transition-colors group-hover:text-primary-fixed-dim">
                  {cite.title}
                </h4>
              </div>
            ))
          )}
        </div>
      </aside>
    </div>
  );
}

function AnswerLine({ line }: { line: string }) {
  if (line.startsWith("### ")) {
    return (
      <h3 className="font-headline-md text-headline-md font-medium text-on-surface">
        {line.replace("### ", "")}
      </h3>
    );
  }
  if (line.startsWith("- ")) {
    return (
      <li className="relative ml-5 list-disc leading-[1.7] text-on-surface/90">
        {line.replace("- ", "")}
      </li>
    );
  }
  if (!line.trim()) return <div className="h-1" />;
  return <p className="font-light leading-[1.7] text-on-surface">{line}</p>;
}

type SseHandlers = {
  onDelta: (delta: string) => void;
  onDone: (payload: Record<string, unknown>) => void;
};

async function consumeSse(
  body: ReadableStream<Uint8Array>,
  handlers: SseHandlers
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      handleSseEvent(rawEvent, handlers);
      separatorIndex = buffer.indexOf("\n\n");
    }
  }
}

function handleSseEvent(rawEvent: string, handlers: SseHandlers): void {
  let eventType = "message";
  const dataLines: string[] = [];
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) eventType = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trim());
  }
  if (dataLines.length === 0) return;
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  } catch {
    return;
  }
  if (eventType === "delta" && typeof parsed.delta === "string") handlers.onDelta(parsed.delta);
  else if (eventType === "done") handlers.onDone(parsed);
}
