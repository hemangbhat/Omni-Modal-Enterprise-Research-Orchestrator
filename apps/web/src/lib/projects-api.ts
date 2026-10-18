/**
 * Client for projects: documents, findings, notes, reports, insights and activity.
 */
import { apiRequest } from "@/lib/api-client";
import { parseApiError } from "@/lib/api-error";
import type { DocumentRecord } from "@/lib/documents-api";
import { getClientApiConfig } from "@/lib/env";
import type { Citation, SessionSummary } from "@/lib/research-api";

export type Project = {
  id: string;
  name: string;
  description: string;
  created_by: string;
  created_at: number;
  updated_at: number;
  archived_at: number | null;
  archived: boolean;
};

export type ProjectCounts = { documents: number; findings: number; notes: number; reports: number };
export type ProjectListEntry = Project & { counts: ProjectCounts };

export type ProjectItem = {
  id: string;
  project_id: string;
  kind: "finding" | "note";
  title: string;
  body: string;
  citations: Citation[];
  source_session_id: string | null;
  created_by: string;
  created_at: number;
  updated_at: number;
};

export type ReportSummary = {
  id: string;
  project_id: string | null;
  source: "project" | "session";
  source_session_id: string | null;
  title: string;
  created_by: string;
  created_at: number;
};
export type Report = ReportSummary & { markdown: string };

export type Insight = { value: string; document_id: string; document_title: string; confidence: number | null };
export type InsightLabel = "decision" | "risk" | "action_item" | "owner" | "deadline";

export type ActivityEntry = {
  id: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  created_at: number;
};

export type ProjectDetail = {
  project: Project;
  documents: DocumentRecord[];
  items: ProjectItem[];
  reports: ReportSummary[];
  sessions: SessionSummary[];
  insights: Record<InsightLabel, Insight[]>;
  activity: ActivityEntry[];
};

export type ProjectSearch = {
  documents: { document_id: string; title: string }[];
  items: { id: string; kind: string; title: string }[];
  reports: { id: string; title: string }[];
};

export class ProjectsApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ProjectsApiError";
    this.status = status;
  }
}

function headers(json = true): Record<string, string> {
  const { token } = getClientApiConfig();
  const out: Record<string, string> = json ? { "Content-Type": "application/json" } : {};
  if (token) out.Authorization = `Bearer ${token}`;
  return out;
}

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const { baseUrl } = getClientApiConfig();
  const res = await apiRequest(
    path,
    { method, headers: headers(), body: body === undefined ? undefined : JSON.stringify(body) },
    { baseUrl }
  );
  if (!res.ok) {
    const { message } = parseApiError(await res.json().catch(() => ({})));
    const fallback =
      res.status === 404
        ? "That project or item no longer exists."
        : res.status === 403
          ? "Your role cannot make this change."
          : `The server returned an error (HTTP ${res.status}).`;
    throw new ProjectsApiError(res.status === 404 || !message ? fallback : message, res.status);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export async function listProjects(includeArchived = false): Promise<ProjectListEntry[]> {
  const data = await call<{ projects: ProjectListEntry[] }>(`/projects${includeArchived ? "?include_archived=true" : ""}`);
  return Array.isArray(data.projects) ? data.projects : [];
}
export const createProject = (name: string, description = "") =>
  call<Project>("/projects", "POST", { name, description });
export const getProject = (id: string) => call<ProjectDetail>(`/projects/${enc(id)}`);
export const updateProject = (id: string, fields: { name?: string; description?: string }) =>
  call<Project>(`/projects/${enc(id)}`, "PATCH", fields);
export const archiveProject = (id: string) => call<Project>(`/projects/${enc(id)}/archive`, "POST");
export const restoreProject = (id: string) => call<Project>(`/projects/${enc(id)}/restore`, "POST");
export const addProjectDocument = (id: string, documentId: string) =>
  call(`/projects/${enc(id)}/documents`, "POST", { document_id: documentId });
export const removeProjectDocument = (id: string, documentId: string) =>
  call(`/projects/${enc(id)}/documents/${enc(documentId)}`, "DELETE");
export const addProjectItem = (
  id: string,
  item: { kind: "finding" | "note"; title: string; body?: string; citations?: Citation[]; source_session_id?: string | null }
) => call<ProjectItem>(`/projects/${enc(id)}/items`, "POST", item);
export const updateProjectItem = (id: string, itemId: string, fields: { title?: string; body?: string }) =>
  call<ProjectItem>(`/projects/${enc(id)}/items/${enc(itemId)}`, "PATCH", fields);
export const deleteProjectItem = (id: string, itemId: string) =>
  call(`/projects/${enc(id)}/items/${enc(itemId)}`, "DELETE");
export const searchProject = (id: string, q: string) =>
  call<ProjectSearch>(`/projects/${enc(id)}/search?q=${enc(q)}`);
export const createProjectReport = (id: string) => call<Report>(`/projects/${enc(id)}/reports`, "POST");
export const createSessionReport = (sessionId: string) =>
  call<Report>(`/research/sessions/${enc(sessionId)}/report`, "POST");
export const setSessionProject = (sessionId: string, projectId: string | null) =>
  call<SessionSummary>(`/research/sessions/${enc(sessionId)}/project`, "PUT", { project_id: projectId });
export const getReport = (id: string) => call<Report>(`/reports/${enc(id)}`);

/** Save a report as a Markdown file. */
export function saveReportFile(report: Report): void {
  const blob = new Blob([report.markdown], { type: "text/markdown;charset=utf-8" });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = `${report.title.replace(/[\\/:*?"<>|]+/g, " ").trim() || "report"}.md`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

/** Plain words for an activity entry. */
export function describeActivity(entry: ActivityEntry): string {
  const title = typeof entry.detail.title === "string" ? `"${entry.detail.title}"` : "";
  switch (entry.action) {
    case "project_created":
      return "Created the project";
    case "project_updated":
      return "Edited the project details";
    case "project_archived":
      return "Archived the project";
    case "project_restored":
      return "Restored the project";
    case "document_added":
      return `Added document ${title}`.trim();
    case "document_removed":
      return "Removed a document";
    case "finding_added":
      return `Saved finding ${title}`.trim();
    case "note_added":
      return `Added note ${title}`.trim();
    case "report_created":
      return `Generated report ${title}`.trim();
    case "session_linked":
      return `Linked research ${title}`.trim();
    default:
      return entry.action.replace(/_/g, " ");
  }
}
