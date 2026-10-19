/**
 * Client for saved research sessions, the streaming research query and the
 * site-wide assistant. Streams are read as server-sent events.
 */
import { apiRequest } from "@/lib/api-client";
import { parseApiError } from "@/lib/api-error";
import { getClientApiConfig } from "@/lib/env";
import { readSse } from "@/lib/sse";

export type Citation = {
  document_id: string;
  chunk_id: string;
  title: string;
  source_type: string;
  chunk_index: number;
  page_number: number | null;
  segment_index: number | null;
  start_ms: number | null;
  end_ms: number | null;
  snippet: string;
  scope: string;
  cited: boolean;
  // Link to the page, for external (web) sources.
  url?: string | null;
};

export type SessionKind = "research" | "assistant";

export type SessionSummary = {
  id: string;
  kind: SessionKind;
  title: string;
  created_at: number;
  updated_at: number;
  /** The project whose documents this session searches, if any. */
  project_id?: string | null;
};

export type SessionMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: string | null;
  citations: Citation[];
  created_at: number;
};

export type SessionDetail = SessionSummary & { messages: SessionMessage[] };

export type ToolEvent = { name: string; label: string; ok: boolean };
export type AssistantAction = { label: string; href: string };

export type ResearchDone = {
  status: "answered" | "no_data" | "failed";
  answer_markdown: string;
  citations: Citation[];
  session_id: string;
  search_query: string | null;
  project_id?: string | null;
};

export type AssistantDone = {
  answer_markdown: string;
  citations: Citation[];
  tools: ToolEvent[];
  actions: AssistantAction[];
  model: string;
  session_id: string;
};

export class ApiRequestError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

function headers(stream = false): Record<string, string> {
  const { token } = getClientApiConfig();
  const out: Record<string, string> = { "Content-Type": "application/json" };
  if (stream) out.Accept = "text/event-stream";
  if (token) out.Authorization = `Bearer ${token}`;
  return out;
}

async function failure(res: Response): Promise<ApiRequestError> {
  const { message } = parseApiError(await res.json().catch(() => ({})));
  const fallback =
    res.status === 402
      ? "You have used this month's queries on your plan. Upgrade on the Billing page to continue."
      : res.status === 404
        ? "This conversation no longer exists."
        : `The server returned an error (HTTP ${res.status}).`;
  const preferFallback = res.status === 402 || res.status === 404 || !message;
  return new ApiRequestError(preferFallback ? fallback : (message as string), res.status);
}

async function call<T>(path: string, method: string, body?: unknown): Promise<T> {
  const { baseUrl } = getClientApiConfig();
  const res = await apiRequest(
    path,
    { method, headers: headers(), body: body === undefined ? undefined : JSON.stringify(body) },
    { baseUrl }
  );
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

export async function listSessions(kind: SessionKind): Promise<SessionSummary[]> {
  const data = await call<{ sessions: SessionSummary[] }>(`/research/sessions?kind=${kind}`, "GET");
  return data.sessions;
}

export function getSession(id: string): Promise<SessionDetail> {
  return call<SessionDetail>(`/research/sessions/${encodeURIComponent(id)}`, "GET");
}

export function renameSession(id: string, title: string): Promise<SessionSummary> {
  return call<SessionSummary>(`/research/sessions/${encodeURIComponent(id)}`, "PATCH", { title });
}

export async function deleteSession(id: string): Promise<void> {
  await call<{ deleted: boolean }>(`/research/sessions/${encodeURIComponent(id)}`, "DELETE");
}

export type StreamHandlers<Done> = {
  onSession?: (sessionId: string) => void;
  onDelta?: (text: string) => void;
  onTool?: (tool: ToolEvent) => void;
  onDone: (done: Done) => void;
};

async function stream<Done>(path: string, body: unknown, handlers: StreamHandlers<Done>): Promise<void> {
  const { baseUrl } = getClientApiConfig();
  const res = await apiRequest(
    path,
    { method: "POST", headers: headers(true), body: JSON.stringify(body) },
    { baseUrl, timeout: 120_000 }
  );
  if (!res.ok || !res.body) throw await failure(res);
  let finished = false;
  let failed: string | null = null;
  await readSse(res.body, (event, data) => {
    if (event === "session" && typeof data.session_id === "string") handlers.onSession?.(data.session_id);
    else if (event === "delta" && typeof data.delta === "string") handlers.onDelta?.(data.delta);
    else if (event === "tool") handlers.onTool?.(data as unknown as ToolEvent);
    else if (event === "error") failed = typeof data.error === "string" ? data.error : "The request failed.";
    else if (event === "done") {
      finished = true;
      handlers.onDone(data as unknown as Done);
    }
  });
  if (failed) throw new ApiRequestError(failed, 500);
  if (!finished) throw new ApiRequestError("The connection closed before the answer finished.", 0);
}

export function streamResearch(
  question: string,
  sessionId: string | null,
  handlers: StreamHandlers<ResearchDone>,
  projectId?: string | null
): Promise<void> {
  // A new session can be limited to a project; an existing one keeps its own scope.
  const scope = sessionId ? { session_id: sessionId } : projectId ? { project_id: projectId } : {};
  return stream("/query/stream", { question, ...scope }, handlers);
}

export function streamAssistant(
  message: string,
  sessionId: string | null,
  page: string,
  handlers: StreamHandlers<AssistantDone>
): Promise<void> {
  return stream("/assistant/stream", { message, page, ...(sessionId ? { session_id: sessionId } : {}) }, handlers);
}

/** Where a citation points inside its source: page, time range, or segment. */
export function citationLocation(c: Citation): string | null {
  if (c.page_number != null) return `Page ${c.page_number}`;
  if (c.start_ms != null) {
    const range = c.end_ms != null ? `${formatMs(c.start_ms)}–${formatMs(c.end_ms)}` : formatMs(c.start_ms);
    return range;
  }
  if (c.segment_index != null) return `Segment ${c.segment_index}`;
  return null;
}

export function formatMs(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** The answer body without the "## Answer" heading and the "## Sources" list (shown as cards instead). */
export function answerBody(markdown: string): string {
  return markdown.split(/\n## Sources\b/)[0].replace(/^## Answer\s*/, "").trim();
}
