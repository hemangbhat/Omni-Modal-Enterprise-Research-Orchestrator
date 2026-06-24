"use client";

import { useRef, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { apiRequest, captureUploadError } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";
import { getActiveWorkspaceId } from "@/lib/workspace";
import type { DocumentStatus } from "@/lib/mock-data";

type UploadItem = {
  id: string;
  name: string;
  size: string;
  type: string;
  status: DocumentStatus;
  error?: string;
};

const acceptedTypes = ["application/pdf", "audio/mpeg", "audio/wav", "audio/x-wav"];

const PIPELINE_STAGES = [
  { key: "uploaded", label: "Uploaded", note: "Secured in staging environment" },
  { key: "processing", label: "OCR / Transcription", note: "Extracting entity text & audio" },
  { key: "vectorizing", label: "Vectorization", note: "Generating semantic embeddings" },
  { key: "ready", label: "Ready", note: "Available for deep research" }
] as const;

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceKindFor(type: string): "pdf" | "audio" | undefined {
  if (type === "application/pdf") return "pdf";
  if (type.startsWith("audio/")) return "audio";
  return undefined;
}

async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

/** Map a job status to the active pipeline stage index. */
function activeStageIndex(status: DocumentStatus | undefined): number {
  switch (status) {
    case "uploaded":
      return 0;
    case "processing":
      return 1;
    case "ready":
      return 3;
    default:
      return 0;
  }
}

export function UploadDropzone() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const latest = items[0];

  function patchItem(id: string, patch: Partial<UploadItem>) {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item))
    );
  }

  async function pollJob(itemId: string, jobId: string, baseUrl: string, token: string | null) {
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        const res = await apiRequest(
          `/ingest/jobs/${jobId}`,
          { method: "GET", headers },
          { baseUrl }
        );
        if (!res.ok) continue;
        const body = (await res.json()) as {
          status: DocumentStatus;
          error_message?: string | null;
        };
        patchItem(itemId, { status: body.status, error: body.error_message ?? undefined });
        if (body.status === "ready" || body.status === "failed") return;
      } catch {
        // transient — keep polling
      }
    }
  }

  async function uploadFile(item: UploadItem, file: File) {
    const { baseUrl, token } = getClientApiConfig();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    try {
      const contentBase64 = await fileToBase64(file);
      const workspaceId = getActiveWorkspaceId();
      const res = await apiRequest(
        "/ingest/upload",
        {
          method: "POST",
          headers,
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
            source_kind: sourceKindFor(file.type),
            ...(workspaceId ? { workspace_id: workspaceId } : {})
          })
        },
        { baseUrl, timeout: 60_000 }
      );
      if (res.status !== 202) {
        captureUploadError({
          file_name: file.name,
          file_size_bytes: file.size,
          http_status: res.status
        });
        let message = `Upload failed (HTTP ${res.status}).`;
        try {
          const body = (await res.json()) as { error?: string };
          if (body.error) message = body.error;
        } catch {
          /* ignore */
        }
        patchItem(item.id, { status: "failed", error: message });
        return;
      }
      const body = (await res.json()) as { job_id: string };
      patchItem(item.id, { status: "processing" });
      await pollJob(item.id, body.job_id, baseUrl, token);
    } catch (err) {
      captureUploadError({
        file_name: file.name,
        file_size_bytes: file.size,
        http_status: "network_error"
      });
      patchItem(item.id, {
        status: "failed",
        error: err instanceof Error ? err.message : "Network error."
      });
    }
  }

  function handleFiles(files: FileList | null) {
    if (!files) return;
    Array.from(files).forEach((file, index) => {
      const id = `${file.name}-${file.lastModified}-${index}`;
      const accepted = acceptedTypes.includes(file.type);
      const item: UploadItem = {
        id,
        name: file.name,
        size: formatBytes(file.size),
        type: file.type || "unknown",
        status: accepted ? "uploaded" : "failed",
        error: accepted ? undefined : "Unsupported file type."
      };
      setItems((current) => [item, ...current]);
      if (accepted) void uploadFile(item, file);
    });
  }

  const activeIndex = activeStageIndex(latest?.status);
  const failed = latest?.status === "failed";

  return (
    <div className="grid grid-cols-1 gap-gutter lg:grid-cols-12">
      {/* Left: dropzone */}
      <div className="space-y-lg lg:col-span-7">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            handleFiles(event.dataTransfer.files);
          }}
          className={`flex min-h-[320px] w-full flex-col items-center justify-center rounded-xl border-2 border-dashed bg-surface-container-low/50 p-12 text-center backdrop-blur-sm transition-all duration-300 ${
            dragging
              ? "border-primary-container bg-surface-container"
              : "border-primary-container/40 hover:border-primary-container hover:bg-surface-container"
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,audio/mpeg,audio/wav,audio/x-wav,.mp3,.wav"
            className="sr-only"
            onChange={(event) => handleFiles(event.target.files)}
          />
          <span className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-primary/10">
            <MaterialIcon name="cloud_upload" size={40} className="text-primary-container" />
          </span>
          <h3 className="mb-2 font-headline-md text-headline-md text-on-surface">
            Drop audio, PDF, or docs here
          </h3>
          <p className="mb-8 max-w-sm font-body-md text-body-md text-on-surface-variant">
            Files are processed locally before secure transport to the indexing engine.
          </p>
          <span className="rounded-lg border border-outline-variant px-6 py-2 font-label-md text-label-md text-primary-container transition-colors hover:bg-primary-container/10">
            Browse Files
          </span>
        </button>

        <div className="flex flex-wrap items-center gap-3">
          <span className="mr-2 font-mono-sm text-[11px] uppercase tracking-widest text-on-surface-variant">
            Supported:
          </span>
          {["PDF", "WAV", "MP3", "DOCX"].map((chip) => (
            <span
              key={chip}
              className="rounded-full border border-outline-variant bg-surface-container-lowest px-3 py-1 font-label-md text-label-md text-on-surface"
            >
              {chip}
            </span>
          ))}
        </div>
      </div>

      {/* Right: active processing + pipeline */}
      <div className="space-y-lg lg:col-span-5">
        <div className="rounded-xl border border-outline-variant bg-surface-container p-6 shadow-lg shadow-black/20">
          <h4 className="mb-4 flex items-center gap-2 font-label-md text-label-md uppercase tracking-widest text-on-surface-variant">
            <MaterialIcon name="sync" size={16} />
            Active Processing
          </h4>
          {items.length === 0 ? (
            <p className="font-body-md text-body-md text-on-surface-variant/70">
              No active uploads. Add a PDF or audio file to begin ingestion.
            </p>
          ) : (
            <div className="space-y-5">
              {items.map((item) => {
                const pct =
                  item.status === "ready"
                    ? 100
                    : item.status === "processing"
                      ? 65
                      : item.status === "failed"
                        ? 100
                        : 20;
                return (
                  <div key={item.id}>
                    <div className="mb-2 flex items-start justify-between">
                      <div className="flex items-center gap-3">
                        <MaterialIcon name="description" className="text-outline" />
                        <div>
                          <p className="w-48 truncate font-body-md text-body-md text-on-surface">
                            {item.name}
                          </p>
                          <p className="font-mono-sm text-[11px] text-on-surface-variant">
                            {item.size} • {item.type}
                          </p>
                        </div>
                      </div>
                      <span
                        className={`font-label-md text-label-md font-bold ${
                          item.status === "failed"
                            ? "text-error"
                            : "text-primary-container text-glow"
                        }`}
                      >
                        {item.status === "failed" ? "Failed" : `${pct}%`}
                      </span>
                    </div>
                    <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-container-highest">
                      <div
                        className={`absolute left-0 top-0 h-full rounded-full transition-all duration-1000 ease-out ${
                          item.status === "failed"
                            ? "bg-error"
                            : "bg-gradient-to-r from-primary to-primary-container shadow-[0_0_10px_rgba(0,209,255,0.8)]"
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    {item.error ? (
                      <p className="mt-1 font-mono-sm text-[11px] text-error">{item.error}</p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-outline-variant bg-surface-container p-6 shadow-lg shadow-black/20">
          <h4 className="mb-6 font-label-md text-label-md uppercase tracking-widest text-on-surface-variant">
            Status Pipeline
          </h4>
          <div className="relative space-y-6 pl-2">
            {PIPELINE_STAGES.map((stage, index) => {
              const done = !failed && index < activeIndex;
              const active = !failed && index === activeIndex;
              const isError = failed && index === activeIndex;
              return (
                <div
                  key={stage.key}
                  className={`step-item relative flex items-start gap-4 ${
                    !done && !active && !isError ? "opacity-50" : ""
                  }`}
                >
                  <div className="stepper-line">
                    <div
                      className={`relative z-10 flex h-6 w-6 items-center justify-center rounded-full border-2 ${
                        isError
                          ? "border-error bg-error/20"
                          : done
                            ? "border-primary-container bg-surface-container"
                            : active
                              ? "border-primary-container bg-primary-container/20 shadow-[0_0_15px_rgba(0,209,255,0.15)]"
                              : "border-outline-variant bg-surface-container"
                      }`}
                    >
                      {isError ? (
                        <MaterialIcon name="close" size={14} className="text-error" />
                      ) : done ? (
                        <MaterialIcon name="check" size={14} className="text-primary-container icon-fill" />
                      ) : active ? (
                        <div className="h-2 w-2 animate-pulse rounded-full bg-primary-container" />
                      ) : null}
                    </div>
                  </div>
                  <div className="pt-0.5">
                    <p
                      className={`font-body-md text-body-md ${
                        active
                          ? "font-medium text-primary-container"
                          : done
                            ? "text-on-surface"
                            : "text-on-surface-variant"
                      }`}
                    >
                      {stage.label}
                    </p>
                    <p className="mt-1 font-mono-sm text-[11px] text-on-surface-variant">
                      {stage.note}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
