"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AnswerMarkdown } from "@/components/answer-markdown";
import { MaterialIcon } from "@/components/material-icon";
import {
  citationLocation,
  streamAssistant,
  type AssistantAction,
  type Citation,
  type ToolEvent
} from "@/lib/research-api";

type Message = {
  key: string;
  role: "user" | "assistant";
  content: string;
  tools: ToolEvent[];
  actions: AssistantAction[];
  citations: Citation[];
  streaming: boolean;
  error: string | null;
};

const SUGGESTIONS: Record<string, string[]> = {
  "/documents": ["Which of my documents are still processing?", "What do my documents say about risks?"],
  "/upload": ["Which of my documents are still processing?", "Which file types can I upload?"],
  "/usage": ["How much of my plan have I used?", "Take me to billing"],
  "/billing": ["How much of my plan have I used?", "Who is on my team?"],
  "/team": ["Who is on my team?", "Take me to settings"],
  "/research": ["Find what my documents say about budget", "Which documents do I have?"]
};
const DEFAULT_SUGGESTIONS = [
  "What can I do in OMERO?",
  "Which documents do I have?",
  "How much of my plan have I used?"
];

export function AssistantPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [highlight, setHighlight] = useState<{ key: string; n: number } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // Focus the input on open; on close, hand focus back to whatever opened it.
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (open) {
      returnFocus.current = document.activeElement as HTMLElement | null;
      inputRef.current?.focus();
    } else if (returnFocus.current) {
      returnFocus.current.focus();
      returnFocus.current = null;
    }
  }, [open]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  function patchLast(patch: (m: Message) => Partial<Message>) {
    setMessages((list) => list.map((m, i) => (i === list.length - 1 ? { ...m, ...patch(m) } : m)));
  }

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    const stamp = Date.now();
    setMessages((list) => [
      ...list,
      { key: `u-${stamp}`, role: "user", content: message, tools: [], actions: [], citations: [], streaming: false, error: null },
      { key: `a-${stamp}`, role: "assistant", content: "", tools: [], actions: [], citations: [], streaming: true, error: null }
    ]);
    setInput("");
    setBusy(true);
    try {
      await streamAssistant(message, sessionId, pathname, {
        onSession: setSessionId,
        onTool: (tool) => patchLast((m) => ({ tools: [...m.tools, tool] })),
        onDelta: (delta) => patchLast((m) => ({ content: m.content + delta })),
        onDone: (done) =>
          patchLast(() => ({
            content: done.answer_markdown,
            actions: done.actions ?? [],
            citations: done.citations ?? [],
            streaming: false
          }))
      });
    } catch (err) {
      patchLast(() => ({
        streaming: false,
        error: err instanceof Error ? err.message : "Could not reach the assistant. Check your connection and try again."
      }));
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    if (busy) return;
    setMessages([]);
    setSessionId(null);
    inputRef.current?.focus();
  }

  const suggestions = SUGGESTIONS[pathname] ?? DEFAULT_SUGGESTIONS;

  return (
    <aside
      aria-label="Assistant"
      inert={!open}
      className={`fixed inset-y-0 right-0 z-[60] flex w-full max-w-[420px] flex-col border-l border-outline-variant/30 bg-surface-container-low shadow-[-24px_0_60px_rgba(0,8,20,0.55)] transition-transform duration-200 ease-out motion-reduce:transition-none ${
        open ? "translate-x-0" : "pointer-events-none translate-x-full"
      }`}
    >
      <header className="flex items-start justify-between gap-md border-b border-outline-variant/20 px-lg py-md">
        <div>
          <h2 className="flex items-center gap-sm font-label-md text-[15px] font-semibold text-on-surface">
            <MaterialIcon name="forum" size={18} className="text-primary-fixed-dim" />
            Assistant
          </h2>
          <p className="mt-[2px] text-[12.5px] text-on-surface-variant/75">
            Answers from your workspace. It can look things up, not change them.
          </p>
        </div>
        <div className="flex items-center gap-xs">
          <button
            type="button"
            onClick={reset}
            disabled={busy || messages.length === 0}
            className="rounded-lg p-xs text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface disabled:opacity-40"
            aria-label="Start a new conversation"
            title="New conversation"
          >
            <MaterialIcon name="add_comment" size={18} />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-xs text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
            aria-label="Close assistant"
          >
            <MaterialIcon name="close" size={18} />
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-lg py-lg">
        {messages.length === 0 ? (
          <div className="flex flex-col gap-md">
            <p className="text-[15px] leading-relaxed text-on-surface-variant">
              Ask about your documents, usage, team, or where to find something. It knows which page you are on.
            </p>
            <div className="flex flex-col items-start gap-sm">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="rounded-xl border border-outline-variant/30 px-md py-sm text-left text-[14px] text-on-surface-variant transition-colors hover:border-primary-fixed-dim/50 hover:text-on-surface"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="flex flex-col gap-lg">
            {messages.map((m) =>
              m.role === "user" ? (
                <li key={m.key} className="self-end rounded-2xl rounded-br-md bg-surface-container-high px-md py-sm text-[15px] text-on-surface">
                  {m.content}
                </li>
              ) : (
                <li key={m.key} className="flex flex-col gap-sm" aria-busy={m.streaming}>
                  {m.tools.length > 0 ? (
                    <ul className="flex flex-wrap gap-xs" aria-label="What the assistant checked">
                      {m.tools.map((t, i) => (
                        <li
                          key={`${t.name}-${i}`}
                          className={`inline-flex items-center gap-[4px] rounded-full px-sm py-[2px] text-[12px] ${
                            t.ok ? "bg-surface-container-high text-on-surface-variant" : "bg-error-container/30 text-error"
                          }`}
                        >
                          <MaterialIcon name={t.ok ? "check" : "block"} size={13} />
                          {t.label}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {m.error ? (
                    <p role="alert" className="text-[14px] text-error">{m.error}</p>
                  ) : m.content ? (
                    <AnswerMarkdown
                      markdown={m.content}
                      citationCount={m.citations.length}
                      activeCitation={highlight?.key === m.key ? highlight.n : null}
                      onCite={(n) => setHighlight({ key: m.key, n })}
                    />
                  ) : (
                    <p className="flex items-center gap-sm text-[14px] text-on-surface-variant">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-primary-fixed-dim" />
                      Looking that up…
                    </p>
                  )}
                  {m.actions.length > 0 ? (
                    <div className="flex flex-wrap gap-sm">
                      {m.actions.map((a) => (
                        <button
                          key={a.href}
                          type="button"
                          onClick={() => {
                            router.push(a.href);
                            onClose();
                          }}
                          className="inline-flex items-center gap-xs rounded-lg bg-primary-fixed-dim px-md py-xs text-[13px] font-semibold text-on-primary-fixed hover:opacity-90"
                        >
                          {a.label}
                          <MaterialIcon name="open_in_new" size={14} />
                        </button>
                      ))}
                    </div>
                  ) : null}
                  {m.citations.length > 0 && !m.streaming ? (
                    <ol className="flex flex-col gap-xs border-t border-outline-variant/20 pt-sm" aria-label="Sources">
                      {m.citations.map((c, i) => {
                        const active = highlight?.key === m.key && highlight.n === i + 1;
                        return (
                          <li
                            key={c.chunk_id || i}
                            className={`flex gap-sm rounded-md px-xs py-[2px] text-[12.5px] ${
                              active ? "bg-primary-fixed-dim/10 text-on-surface" : "text-on-surface-variant/80"
                            }`}
                          >
                            <span className="font-mono-sm text-primary-fixed-dim">{i + 1}</span>
                            <span className="min-w-0">
                              <span className="font-medium">{c.title}</span>
                              {citationLocation(c) ? `, ${citationLocation(c)}` : ""}
                              {active && c.snippet ? (
                                <span className="mt-[2px] block text-on-surface-variant">{c.snippet}</span>
                              ) : null}
                            </span>
                          </li>
                        );
                      })}
                    </ol>
                  ) : null}
                </li>
              )
            )}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="border-t border-outline-variant/20 p-md"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <div className="flex items-end gap-sm rounded-xl border border-outline-variant/30 bg-surface px-sm py-xs focus-within:border-primary-fixed-dim/50">
          <label htmlFor="assistant-input" className="sr-only">
            Message the assistant
          </label>
          <textarea
            id="assistant-input"
            ref={inputRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="Ask about your workspace…"
            className="max-h-32 min-h-[40px] flex-1 resize-none border-none bg-transparent px-xs py-[9px] text-[15px] text-on-surface placeholder-on-surface-variant/50 focus:outline-none focus:ring-0"
          />
          <button
            type="submit"
            disabled={!input.trim() || busy}
            className="mb-[2px] flex h-[36px] w-[36px] items-center justify-center rounded-lg bg-primary-fixed-dim text-on-primary-fixed disabled:opacity-40"
            aria-label="Send"
          >
            <MaterialIcon name="arrow_upward" size={18} />
          </button>
        </div>
      </form>
    </aside>
  );
}
