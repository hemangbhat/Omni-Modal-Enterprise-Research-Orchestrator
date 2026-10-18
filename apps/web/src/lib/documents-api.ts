/**
 * Client for durable uploads and document actions.
 *
 * Upload flow: hash the file in the browser, ask the API for an upload slot
 * (or learn it is already in the library), send the bytes straight to object
 * storage (or to the API's own route when none is configured), confirm, then
 * poll the document until processing finishes.
 */
import { apiRequest } from "@/lib/api-client";
import { parseApiError } from "@/lib/api-error";
import { getClientApiConfig } from "@/lib/env";

export type JobStage =
  | "awaiting_upload"
  | "queued"
  | "extracting"
  | "transcribing"
  | "chunking"
  | "embedding"
  | "done"
  | "failed";

export type DocumentStatus = "uploaded" | "processing" | "ready" | "failed" | "archived";

export type DocumentRecord = {
  id: string;
  document_id: string;
  title: string;
  file_name: string;
  source_kind: "pdf" | "audio" | string;
  content_type: string;
  size_bytes: number;
  content_hash: string;
  status: DocumentStatus;
  error_message: string | null;
  chunk_count: number;
  uploaded_by: string;
  created_at: number;
  updated_at: number;
  processed_at: number | null;
  archived_at?: number | null;
};

export type JobRecord = {
  id: string;
  job_id: string;
  document_id: string;
  stage: JobStage;
  status: DocumentStatus;
  attempts: number;
  error_message: string | null;
};

export type DocumentDetail = { document: DocumentRecord; job: JobRecord | null };

// PUT to the API's own route, or a form POST straight to object storage whose
// signed policy only accepts exactly this file's size.
type UploadTarget =
  | { method: "PUT"; url: string; requires_auth: true }
  | { method: "POST"; url: string; fields: Record<string, string>; requires_auth: false };
type SlotResponse = DocumentDetail & { duplicate: boolean; resumed?: boolean; upload: UploadTarget | null };

export class DocumentsApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "DocumentsApiError";
    this.status = status;
  }
}

function authHeaders(json = true): Record<string, string> {
  const { token } = getClientApiConfig();
  const headers: Record<string, string> = json ? { "Content-Type": "application/json" } : {};
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function failure(res: Response): Promise<DocumentsApiError> {
  const { message } = parseApiError(await res.json().catch(() => ({})));
  const fallback =
    res.status === 402
      ? "You have reached this month's upload limit on your plan. Upgrade on the Billing page."
      : res.status === 404
        ? "That document no longer exists."
        : `The server returned an error (HTTP ${res.status}).`;
  return new DocumentsApiError(res.status === 402 || !message ? fallback : message, res.status);
}

async function call<T>(path: string, method: string, body?: unknown): Promise<T> {
  const { baseUrl } = getClientApiConfig();
  const res = await apiRequest(
    path,
    { method, headers: authHeaders(), body: body === undefined ? undefined : JSON.stringify(body) },
    { baseUrl }
  );
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

/** SHA-256 of the file, as lowercase hex (lets the server skip files it already has). */
export async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function createUploadSlot(
  file: File,
  sha256: string,
  workspaceId?: string | null
): Promise<SlotResponse> {
  return call<SlotResponse>("/documents/uploads", "POST", {
    filename: file.name,
    size_bytes: file.size,
    content_type: file.type || "application/octet-stream",
    sha256,
    ...(workspaceId ? { workspace_id: workspaceId } : {})
  });
}

/** Send the bytes with upload progress (fetch cannot report upload progress). */
export function sendFile(target: UploadTarget, file: File, onProgress: (fraction: number) => void): Promise<void> {
  const { baseUrl } = getClientApiConfig();
  const url = target.requires_auth ? `${baseUrl}${target.url}` : target.url;
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(target.method, url);
    let body: Blob | FormData = file;
    if (target.method === "POST") {
      // Object storage form upload: the signed fields first, the file last.
      const form = new FormData();
      for (const [name, value] of Object.entries(target.fields)) form.append(name, value);
      form.append("file", file);
      body = form;
    } else {
      for (const [name, value] of Object.entries(authHeaders(false))) xhr.setRequestHeader(name, value);
      if (file.type) xhr.setRequestHeader("Content-Type", file.type);
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1);
        resolve();
        return;
      }
      let message = `Sending the file failed (HTTP ${xhr.status}).`;
      try {
        message = parseApiError(JSON.parse(xhr.responseText)).message ?? message;
      } catch {
        /* storage services answer in XML; keep the generic message */
      }
      reject(new DocumentsApiError(message, xhr.status));
    };
    xhr.onerror = () =>
      reject(new DocumentsApiError("The connection dropped while sending the file. Try again.", 0));
    xhr.send(body);
  });
}

export function completeUpload(documentId: string): Promise<DocumentDetail> {
  return call<DocumentDetail>(`/documents/${encodeURIComponent(documentId)}/complete`, "POST");
}

export function getDocument(documentId: string): Promise<DocumentDetail> {
  return call<DocumentDetail>(`/documents/${encodeURIComponent(documentId)}`, "GET");
}

export function deleteDocument(documentId: string): Promise<{ deleted: boolean }> {
  return call(`/documents/${encodeURIComponent(documentId)}`, "DELETE");
}

export function archiveDocument(documentId: string): Promise<{ document: DocumentRecord }> {
  return call(`/documents/${encodeURIComponent(documentId)}/archive`, "POST");
}

export function restoreDocument(documentId: string): Promise<{ document: DocumentRecord }> {
  return call(`/documents/${encodeURIComponent(documentId)}/restore`, "POST");
}

export function reprocessDocument(documentId: string): Promise<DocumentDetail> {
  return call<DocumentDetail>(`/documents/${encodeURIComponent(documentId)}/reprocess`, "POST");
}

/** Save the original file: a short-lived storage link, or an authenticated API download. */
export async function downloadDocument(documentId: string): Promise<void> {
  const link = await call<{ url: string; requires_auth: boolean; file_name: string }>(
    `/documents/${encodeURIComponent(documentId)}/download`,
    "GET"
  );
  let href = link.url;
  let revoke: string | null = null;
  if (link.requires_auth) {
    const { baseUrl } = getClientApiConfig();
    const res = await apiRequest(link.url, { method: "GET", headers: authHeaders(false) }, { baseUrl, timeout: 120_000 });
    if (!res.ok) throw await failure(res);
    href = URL.createObjectURL(await res.blob());
    revoke = href;
  }
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = link.file_name;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  if (revoke) setTimeout(() => URL.revokeObjectURL(revoke as string), 10_000);
}

/** Plain-language name of a processing stage. */
export function stageLabel(stage: JobStage | undefined | null): string {
  switch (stage) {
    case "awaiting_upload":
      return "Uploading";
    case "queued":
      return "Waiting to process";
    case "extracting":
      return "Reading text";
    case "transcribing":
      return "Transcribing audio";
    case "chunking":
      return "Splitting into passages";
    case "embedding":
      return "Indexing for search";
    case "done":
      return "Ready";
    case "failed":
      return "Failed";
    default:
      return "Waiting";
  }
}

export function isFinal(stage: JobStage | undefined | null): boolean {
  return stage === "done" || stage === "failed";
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
