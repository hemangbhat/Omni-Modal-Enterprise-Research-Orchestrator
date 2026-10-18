"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnswerMarkdown } from "@/components/answer-markdown";
import { EvidenceCard } from "@/components/evidence-card";
import { MaterialIcon } from "@/components/material-icon";
import { captureQueryError } from "@/lib/api-client";
import {
  ApiRequestError,
  answerBody,
  deleteSession,
  getSession,
  listSessions,
  renameSession,
  streamResearch,
  type Citation,
  type SessionDetail,
  type SessionSummary
} from "@/lib/research-api";

type Turn = {
  key: string;
  question: string;
  answer: string;
  status: string | null;
  citations: Citation[];
  streaming: boolean;
  error: string | null;
};

const EXAMPLES = [
  "Summarize the key decisions in my most recent document",
  "What risks or open issues are mentioned?",
  "Which documents talk about budget or spending?"
];

function turnsFrom(detail: SessionDetail): Turn[] {
  const turns: Turn[] = [];
  for (const m of detail.messages) {
    if (m.role === "user") {
      turns.push({ key: m.id, question: m.content, answer: "", status: null, citations: [], streaming: false, error: null });
    } else if (turns.length) {
      const last = turns[turns.length - 1];
      last.answer = m.content;
      last.status = m.status;
      last.citations = m.citations ?? [];
    }
  }
  return turns;
}

function relativeTime(epochSeconds: number): string {
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function ResearchChat() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loadingSession, setLoadingSession] = useState(false);
  const [selected, setSelected] = useState<number>(-1);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSessions, setShowSessions] = useState(false);
  const threadEnd = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions("research"));
      setSessionsError(null);
    } catch (err) {
      setSessionsError(err instanceof Error ? err.message : "Could not load saved research.");
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length]);

  const shownTurn = turns[selected >= 0 ? selected : turns.length - 1];

  async function openSession(id: string) {
    if (busy || id === activeId) return;
    setShowSessions(false);
    setActiveId(id);
    setLoadingSession(true);
    setTurns([]);
    setSelected(-1);
    setActiveCitation(null);
    try {
      setTurns(turnsFrom(await getSession(id)));
    } catch (err) {
      setSessionsError(err instanceof Error ? err.message : "Could not open this research.");
      setActiveId(null);
      void refreshSessions();
    } finally {
      setLoadingSession(false);
    }
  }

  function startNew() {
    if (busy) return;
    setActiveId(null);
    setTurns([]);
    setSelected(-1);
    setActiveCitation(null);
    setShowSessions(false);
    inputRef.current?.focus();
  }

  function patchTurn(index: number, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) {
    setTurns((current) =>
      current.map((t, i) => (i === index ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t))
    );
  }

  async function submit(text?: string) {
    const question = (text ?? prompt).trim();
    if (!question || busy) return;
    const index = turns.length;
    setTurns((current) => [
      ...current,
      { key: `pending-${Date.now()}`, question, answer: "", status: null, citations: [], streaming: true, error: null }
    ]);
    setSelected(-1);
    setActiveCitation(null);
    setPrompt("");
    setBusy(true);
    try {
      await streamResearch(question, activeId, {
        onSession: (id) => setActiveId(id),
        onDelta: (delta) => patchTurn(index, (t) => ({ answer: t.answer + delta })),
        onDone: (done) =>
          patchTurn(index, {
            answer: done.answer_markdown,
            status: done.status,
            citations: done.citations ?? [],
            streaming: false
          })
      });
    } catch (err) {
      const status = err instanceof ApiRequestError ? err.status : "network_error";
      captureQueryError({ query_length: question.length, http_status: status === 0 ? "network_error" : status });
      patchTurn(index, {
        streaming: false,
        error: err instanceof Error ? err.message : "Could not reach the research service. Check your connection and try again."
      });
    } finally {
      setBusy(false);
      void refreshSessions();
    }
  }

  function cite(turnIndex: number, n: number) {
    setSelected(turnIndex);
    setActiveCitation(n);
    // After the evidence column has re-rendered for this turn.
    window.setTimeout(() => {
      document.getElementById(`evidence-${n}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 0);
  }

  const activeTitle = sessions.find((s) => s.id === activeId)?.title;

  return (
    <div className="flex h-[calc(100vh-4rem)] w-full md:h-screen">
      {/* Saved research */}
      <aside
        className={`${showSessions ? "fixed inset-x-0 bottom-0 top-16 z-40 flex" : "hidden"} w-full flex-col border-r border-outline-variant/20 bg-surface-container-lowest md:top-0 lg:static lg:flex lg:w-[280px] lg:bg-surface-container-lowest/40`}
        aria-label="Saved research"
      >
        <div className="flex items-center justify-between gap-sm px-lg pb-md pt-lg">
          <h2 className="font-label-md text-label-md font-semibold text-on-surface">Saved research</h2>
          <button
            type="button"
            onClick={startNew}
            disabled={busy}
            className="flex items-center gap-xs rounded-lg border border-outline-variant/40 px-md py-xs font-label-md text-[13px] text-primary transition-colors hover:border-primary-fixed-dim/60 disabled:opacity-50"
          >
            <MaterialIcon name="add" size={16} />
            New
          </button>
        </div>
        <div className="flex flex-1 flex-col gap-xs overflow-y-auto px-sm pb-lg">
          {sessionsLoading ? (
            <p className="px-md py-sm text-[14px] text-on-surface-variant/70">Loading…</p>
          ) : sessionsError ? (
            <p className="px-md py-sm text-[14px] text-error">{sessionsError}</p>
          ) : sessions.length === 0 ? (
            <p className="px-md py-sm text-[14px] leading-relaxed text-on-surface-variant/70">
              Questions you ask are saved here, so you can come back and continue them.
            </p>
          ) : (
            sessions.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                active={s.id === activeId}
                disabled={busy}
                onOpen={() => void openSession(s.id)}
                onRename={async (title) => {
                  const updated = await renameSession(s.id, title);
                  setSessions((list) => list.map((x) => (x.id === s.id ? updated : x)));
                }}
                onDelete={async () => {
                  await deleteSession(s.id);
                  setSessions((list) => list.filter((x) => x.id !== s.id));
                  if (s.id === activeId) startNew();
                }}
              />
            ))
          )}
        </div>
      </aside>

      {/* Thread */}
      <section className="relative flex min-w-0 flex-1 flex-col bg-surface">
        <div className="flex items-center gap-md border-b border-outline-variant/15 px-lg py-md">
          <button
            type="button"
            onClick={() => setShowSessions((v) => !v)}
            className="rounded-lg p-xs text-on-surface-variant hover:bg-surface-container-high lg:hidden"
            aria-label={showSessions ? "Hide saved research" : "Show saved research"}
          >
            <MaterialIcon name={showSessions ? "close" : "history"} size={20} />
          </button>
          <h1 className="truncate font-headline-md text-[18px] font-semibold text-on-surface">
            {activeTitle ?? (turns.length ? turns[0].question : "Research")}
          </h1>
        </div>

        <div className="flex-1 overflow-y-auto px-lg pb-[180px] pt-xl md:px-xl">
          <div className="mx-auto flex max-w-[720px] flex-col gap-[56px]">
            {loadingSession ? (
              <p className="text-on-surface-variant">Opening saved research…</p>
            ) : turns.length === 0 ? (
              <EmptyThread onPick={(q) => void submit(q)} disabled={busy} />
            ) : (
              turns.map((turn, i) => (
                <TurnView
                  key={turn.key}
                  turn={turn}
                  selected={shownTurn === turn}
                  activeCitation={shownTurn === turn ? activeCitation : null}
                  onSelect={() => {
                    setSelected(i);
                    setActiveCitation(null);
                  }}
                  onCite={(n) => cite(i, n)}
                />
              ))
            )}
            <div ref={threadEnd} />
          </div>
        </div>

        {/* Composer */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-surface via-surface/95 to-transparent px-lg pb-lg pt-[64px] md:px-xl">
          <form
            className="pointer-events-auto mx-auto max-w-[720px]"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div className="flex items-end gap-sm rounded-2xl border border-outline-variant/30 bg-surface-container-low p-sm shadow-[0_12px_40px_rgba(0,8,20,0.5)] focus-within:border-primary-fixed-dim/50">
              <label htmlFor="research-input" className="sr-only">
                Ask a question about your documents
              </label>
              <textarea
                id="research-input"
                ref={inputRef}
                rows={1}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void submit();
                  }
                }}
                placeholder={turns.length ? "Ask a follow-up…" : "Ask about your documents…"}
                className="max-h-40 min-h-[44px] flex-1 resize-none border-none bg-transparent px-md py-[10px] text-[16px] text-on-surface placeholder-on-surface-variant/50 focus:outline-none focus:ring-0"
              />
              <button
                type="submit"
                disabled={!prompt.trim() || busy}
                className="flex h-[44px] items-center gap-xs rounded-xl bg-primary-fixed-dim px-lg font-label-md text-label-md font-semibold text-on-primary-fixed transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? "Answering" : "Ask"}
                <MaterialIcon name="arrow_upward" size={18} />
              </button>
            </div>
            <p className="mt-xs px-sm text-[12px] text-on-surface-variant/60">
              Answers use only your workspace&apos;s documents. Follow-ups remember this conversation.
            </p>
          </form>
        </div>
      </section>

      {/* Evidence */}
      <aside
        className="hidden w-[360px] flex-col border-l border-outline-variant/20 bg-surface-container-lowest/40 xl:flex"
        aria-label="Evidence"
      >
        <div className="px-lg pb-md pt-lg">
          <h2 className="font-label-md text-label-md font-semibold text-on-surface">Evidence</h2>
          <p className="mt-xs text-[13px] text-on-surface-variant/70">
            {shownTurn ? `Sources for: ${shownTurn.question}` : "Passages behind each answer appear here."}
          </p>
        </div>
        <div className="flex flex-1 flex-col gap-md overflow-y-auto px-lg pb-xl">
          {shownTurn && shownTurn.citations.length > 0
            ? shownTurn.citations.map((c, i) => (
                <EvidenceCard key={c.chunk_id || i} citation={c} index={i + 1} active={activeCitation === i + 1} anchor />
              ))
            : shownTurn && !shownTurn.streaming
              ? <p className="text-[14px] text-on-surface-variant/70">This answer has no supporting passages.</p>
              : null}
        </div>
      </aside>
    </div>
  );
}

function EmptyThread({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  return (
    <div className="flex flex-col gap-lg pt-xl">
      <h2 className="max-w-[18ch] font-headline-lg text-[34px] font-semibold leading-[1.15] tracking-tight text-on-surface">
        Ask anything your documents can answer.
      </h2>
      <p className="max-w-[56ch] text-[16px] leading-relaxed text-on-surface-variant">
        Every answer cites the passages it came from, with page numbers or audio timestamps. If your documents
        don&apos;t contain the answer, OMERO says so instead of guessing.
      </p>
      <div className="flex flex-col items-start gap-sm">
        {EXAMPLES.map((q) => (
          <button
            key={q}
            type="button"
            disabled={disabled}
            onClick={() => onPick(q)}
            className="rounded-xl border border-outline-variant/30 px-md py-sm text-left text-[15px] text-on-surface-variant transition-colors hover:border-primary-fixed-dim/50 hover:text-on-surface disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

function TurnView({
  turn,
  selected,
  activeCitation,
  onSelect,
  onCite
}: {
  turn: Turn;
  selected: boolean;
  activeCitation: number | null;
  onSelect: () => void;
  onCite: (n: number) => void;
}) {
  const body = answerBody(turn.answer);
  const noData = turn.status === "no_data";
  return (
    <article className="flex flex-col gap-lg" aria-busy={turn.streaming}>
      <div className="flex items-start justify-between gap-md">
        <h2 className="text-[21px] font-semibold leading-snug tracking-tight text-on-surface">{turn.question}</h2>
        {!selected && !turn.streaming && turn.citations.length > 0 ? (
          <button
            type="button"
            onClick={onSelect}
            className="mt-[4px] hidden shrink-0 rounded-lg px-sm py-[2px] text-[13px] text-primary-fixed-dim hover:bg-surface-container-high xl:inline-flex"
          >
            Show sources
          </button>
        ) : null}
      </div>
      <div
        className={`-mx-md rounded-2xl border-l-2 px-md transition-colors ${
          selected ? "border-primary-fixed-dim/70" : "border-transparent"
        }`}
      >
        {turn.error ? (
          <p role="alert" className="rounded-xl border border-error/30 bg-error-container/20 px-md py-sm text-[15px] text-error">
            {turn.error}
          </p>
        ) : body ? (
          <div className={noData ? "text-on-surface-variant" : undefined}>
            {noData ? (
              <p className="mb-sm flex items-center gap-xs text-[13px] font-medium text-on-surface-variant">
                <MaterialIcon name="search_off" size={16} />
                Not enough evidence
              </p>
            ) : null}
            <AnswerMarkdown
              markdown={body}
              citationCount={turn.streaming ? 0 : turn.citations.length}
              activeCitation={activeCitation}
              onCite={onCite}
            />
          </div>
        ) : (
          <p className="flex items-center gap-sm text-[15px] text-on-surface-variant">
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary-fixed-dim" />
            Searching your documents…
          </p>
        )}
        {turn.streaming && body ? (
          <span className="ml-xs inline-block h-4 w-[3px] animate-pulse bg-primary-fixed-dim align-middle" />
        ) : null}
      </div>
      {/* Evidence inline on screens without the side column */}
      {turn.citations.length > 0 ? (
        <details className="xl:hidden">
          <summary className="cursor-pointer text-[14px] text-primary-fixed-dim">
            {turn.citations.length} source{turn.citations.length === 1 ? "" : "s"}
          </summary>
          <div className="mt-md flex flex-col gap-md">
            {turn.citations.map((c, i) => (
              <EvidenceCard key={c.chunk_id || i} citation={c} index={i + 1} active={false} />
            ))}
          </div>
        </details>
      ) : null}
    </article>
  );
}

function SessionRow({
  session,
  active,
  disabled,
  onOpen,
  onRename,
  onDelete
}: {
  session: SessionSummary;
  active: boolean;
  disabled: boolean;
  onOpen: () => void;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"view" | "rename" | "confirm">("view");
  const [title, setTitle] = useState(session.title);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<void>) {
    try {
      await action();
      setMode("view");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work. Try again.");
    }
  }

  if (mode === "rename") {
    return (
      <form
        className="rounded-xl bg-surface-container-high px-md py-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (title.trim()) void run(() => onRename(title.trim()));
        }}
      >
        <label className="sr-only" htmlFor={`rename-${session.id}`}>
          Research title
        </label>
        <input
          id={`rename-${session.id}`}
          autoFocus
          value={title}
          maxLength={120}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setMode("view")}
          className="w-full rounded-md border border-outline-variant/40 bg-surface px-sm py-xs text-[14px] text-on-surface focus:border-primary-fixed-dim focus:outline-none"
        />
        <div className="mt-xs flex gap-sm text-[13px]">
          <button type="submit" className="text-primary-fixed-dim">Save</button>
          <button type="button" onClick={() => setMode("view")} className="text-on-surface-variant">Cancel</button>
        </div>
        {error ? <p className="mt-xs text-[12px] text-error">{error}</p> : null}
      </form>
    );
  }

  return (
    <div
      className={`group relative rounded-xl transition-colors ${
        active ? "bg-surface-container-high" : "hover:bg-surface-container-low"
      }`}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={disabled && !active}
        className="flex w-full flex-col gap-[2px] px-md py-sm pr-[64px] text-left"
      >
        <span className={`line-clamp-2 text-[14px] leading-snug ${active ? "text-on-surface" : "text-on-surface-variant"}`}>
          {session.title}
        </span>
        <span className="text-[12px] text-on-surface-variant/60">{relativeTime(session.updated_at)}</span>
      </button>
      {mode === "confirm" ? (
        <div className="flex items-center gap-sm px-md pb-sm text-[13px]">
          <span className="text-on-surface-variant">Delete this research?</span>
          <button type="button" disabled={disabled} onClick={() => void run(onDelete)} className="text-error disabled:opacity-50">
            Delete
          </button>
          <button type="button" onClick={() => setMode("view")} className="text-on-surface-variant">Keep</button>
        </div>
      ) : disabled ? null : (
        // Hidden while an answer streams: renaming or deleting mid-answer would
        // leave the open conversation pointing at a session that changed.
        <div className="absolute right-xs top-xs flex opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <button
            type="button"
            onClick={() => {
              setTitle(session.title);
              setMode("rename");
            }}
            aria-label={`Rename ${session.title}`}
            className="rounded-md p-xs text-on-surface-variant hover:text-on-surface"
          >
            <MaterialIcon name="edit" size={16} />
          </button>
          <button
            type="button"
            onClick={() => setMode("confirm")}
            aria-label={`Delete ${session.title}`}
            className="rounded-md p-xs text-on-surface-variant hover:text-error"
          >
            <MaterialIcon name="delete" size={16} />
          </button>
        </div>
      )}
      {error ? <p className="px-md pb-sm text-[12px] text-error">{error}</p> : null}
    </div>
  );
}
