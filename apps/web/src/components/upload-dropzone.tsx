"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { captureUploadError } from "@/lib/api-client";
import {
  DocumentsApiError,
  completeUpload,
  createUploadSlot,
  formatBytes,
  getDocument,
  isFinal,
  sendFile,
  sha256Hex,
  stageLabel,
  type JobStage
} from "@/lib/documents-api";
import { ALLOWED_EXTENSIONS, validateUploadFile } from "@/hooks/useUploadValidation";
import { getActiveWorkspaceId } from "@/lib/workspace";

type Phase = "checking" | "uploading" | JobStage | "duplicate" | "rejected";

type UploadItem = {
  key: string;
  name: string;
  size: number;
  phase: Phase;
  progress: number; // 0..1 while the bytes are being sent
  documentId?: string;
  error?: string;
};

// The steps a file goes through, in order, as the user sees them.
const STEPS: { label: string; phases: Phase[] }[] = [
  { label: "Upload", phases: ["checking", "uploading", "awaiting_upload"] },
  { label: "Read", phases: ["queued", "extracting", "transcribing"] },
  { label: "Split", phases: ["chunking"] },
  { label: "Index", phases: ["embedding"] },
  { label: "Ready", phases: ["done", "duplicate"] }
];

function stepIndex(phase: Phase): number {
  const index = STEPS.findIndex((step) => step.phases.includes(phase));
  return index === -1 ? 0 : index;
}

function phaseText(item: UploadItem): string {
  if (item.phase === "checking") return "Checking file";
  if (item.phase === "uploading") return `Uploading ${Math.round(item.progress * 100)}%`;
  if (item.phase === "duplicate") return "Already in your library";
  if (item.phase === "rejected") return "Not uploaded";
  return stageLabel(item.phase);
}

const POLL_MS = 1500;
const POLL_LIMIT_MS = 10 * 60 * 1000;

export function UploadDropzone() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Polling stops when the user leaves the page.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function patch(key: string, change: Partial<UploadItem>) {
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...change } : item)));
  }

  async function follow(key: string, documentId: string) {
    const started = Date.now();
    while (Date.now() - started < POLL_LIMIT_MS) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      if (!mounted.current) return;
      try {
        const { document, job } = await getDocument(documentId);
        const stage = job?.stage ?? "queued";
        patch(key, { phase: stage, error: stage === "failed" ? document.error_message ?? "Processing failed." : undefined });
        if (isFinal(stage)) return;
      } catch (err) {
        if (err instanceof DocumentsApiError && err.status === 404) {
          patch(key, { phase: "failed", error: "This document was deleted." });
          return;
        }
        // Temporary network trouble: keep polling.
      }
    }
    patch(key, { error: "Still processing. Check the Documents page later." });
  }

  async function upload(key: string, file: File) {
    try {
      const sha256 = await sha256Hex(file);
      const slot = await createUploadSlot(file, sha256, getActiveWorkspaceId());
      if (slot.duplicate || !slot.upload) {
        // The same bytes are already known: report what state that copy is in.
        const existing = slot.document;
        if (existing.status === "ready") {
          patch(key, { phase: "duplicate", documentId: existing.id, progress: 1 });
        } else if (existing.status === "failed") {
          patch(key, {
            phase: "failed",
            documentId: existing.id,
            error: `This file is already in your library but could not be processed${
              existing.error_message ? `: ${existing.error_message}` : "."
            } Use Reprocess on the Documents page.`
          });
        } else {
          patch(key, { phase: slot.job?.stage ?? "queued", documentId: existing.id, progress: 1 });
          await follow(key, existing.id);
        }
        return;
      }
      patch(key, { phase: "uploading", documentId: slot.document.id });
      await sendFile(slot.upload, file, (progress) => patch(key, { progress }));
      const done = await completeUpload(slot.document.id);
      patch(key, { phase: done.job?.stage ?? "queued" });
      await follow(key, slot.document.id);
    } catch (err) {
      const status = err instanceof DocumentsApiError && err.status ? err.status : "network_error";
      captureUploadError({ file_name: file.name, file_size_bytes: file.size, http_status: status });
      patch(key, {
        phase: "failed",
        error: err instanceof Error ? err.message : "The upload failed. Check your connection and try again."
      });
    }
  }

  function handleFiles(files: FileList | null) {
    if (!files) return;
    const added: UploadItem[] = [];
    Array.from(files).forEach((file, index) => {
      const key = `${file.name}-${file.size}-${file.lastModified}-${Date.now()}-${index}`;
      const problem = validateUploadFile(file);
      added.push({
        key,
        name: file.name,
        size: file.size,
        phase: problem ? "rejected" : "checking",
        progress: 0,
        error: problem ?? undefined
      });
      if (!problem) void upload(key, file);
    });
    setItems((current) => [...added, ...current]);
  }

  return (
    <div className="grid grid-cols-1 gap-gutter lg:grid-cols-12">
      <div className="space-y-md lg:col-span-6">
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={Array.from(ALLOWED_EXTENSIONS).join(",")}
          className="sr-only"
          tabIndex={-1}
          aria-label="Choose files to upload"
          onChange={(event) => {
            handleFiles(event.target.files);
            event.target.value = "";
          }}
        />
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
          className={`flex min-h-[300px] w-full flex-col items-center justify-center rounded-2xl border-2 border-dashed p-xl text-center transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-fixed-dim ${
            dragging
              ? "border-primary-fixed-dim bg-primary-fixed-dim/[0.06]"
              : "border-outline-variant/60 bg-surface-container-low/60 hover:border-primary-fixed-dim/60"
          }`}
        >

          <MaterialIcon name="upload_file" size={40} className="mb-md text-primary-fixed-dim" />
          <span className="mb-xs text-[19px] font-semibold text-on-surface">Drop PDFs or recordings here</span>
          <span className="mb-lg max-w-sm text-[15px] leading-relaxed text-on-surface-variant">
            or click to choose files. Up to 50 MB each.
          </span>
          <span className="rounded-lg border border-outline-variant px-lg py-sm text-[14px] font-medium text-primary">
            Choose files
          </span>
        </button>
        <p className="text-[13px] leading-relaxed text-on-surface-variant/80">
          Accepted: PDF, MP3, WAV, M4A, FLAC, OGG and WEBM. A file that is already in your library is recognised and
          not processed twice.
        </p>
      </div>

      <section className="lg:col-span-6" aria-label="Uploads">
        <h3 className="mb-md text-[15px] font-semibold text-on-surface">Uploads</h3>
        {items.length === 0 ? (
          <p className="rounded-2xl border border-outline-variant/30 p-lg text-[14px] leading-relaxed text-on-surface-variant">
            Files you add appear here with each processing step as it happens.
          </p>
        ) : (
          <ul className="flex flex-col gap-md">
            {items.map((item) => (
              <UploadRow key={item.key} item={item} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const failed = item.phase === "failed" || item.phase === "rejected";
  const finished = item.phase === "done" || item.phase === "duplicate";
  const current = stepIndex(item.phase);
  return (
    <li className="rounded-2xl border border-outline-variant/30 bg-surface-container-low p-md" aria-live="polite">
      <div className="mb-sm flex items-start justify-between gap-md">
        <div className="min-w-0">
          <p className="truncate text-[15px] font-medium text-on-surface" title={item.name}>
            {item.name}
          </p>
          <p className="text-[12px] text-on-surface-variant/80">{formatBytes(item.size)}</p>
        </div>
        <span
          className={`shrink-0 text-[13px] font-medium ${
            failed ? "text-error" : finished ? "text-primary-fixed-dim" : "text-on-surface-variant"
          }`}
        >
          {phaseText(item)}
        </span>
      </div>

      <ol className="grid grid-cols-5 gap-xs" aria-label="Processing steps">
        {STEPS.map((step, index) => {
          const done = finished || (!failed && index < current);
          const active = !failed && !finished && index === current;
          const broken = failed && index === current;
          const width = active && index === 0 && item.phase === "uploading" ? `${Math.round(item.progress * 100)}%` : undefined;
          return (
            <li key={step.label} className="flex flex-col gap-[6px]">
              <span className="relative h-[5px] overflow-hidden rounded-full bg-surface-container-highest">
                <span
                  className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-300 ${
                    broken ? "bg-error" : done || active ? "bg-primary-fixed-dim" : ""
                  } ${active && !width ? "animate-pulse" : ""}`}
                  style={{ width: broken || done ? "100%" : active ? width ?? "100%" : "0%" }}
                />
              </span>
              <span
                className={`text-[11px] ${
                  broken ? "text-error" : done || active ? "text-on-surface" : "text-on-surface-variant/60"
                }`}
              >
                {step.label}
              </span>
            </li>
          );
        })}
      </ol>

      {item.error ? (
        <p role="alert" className="mt-sm text-[13px] leading-snug text-error">
          {item.error}
        </p>
      ) : null}
      {finished && item.documentId ? (
        <Link href="/documents" className="mt-sm inline-block text-[13px] text-primary-fixed-dim hover:underline">
          View in Documents
        </Link>
      ) : null}
    </li>
  );
}
