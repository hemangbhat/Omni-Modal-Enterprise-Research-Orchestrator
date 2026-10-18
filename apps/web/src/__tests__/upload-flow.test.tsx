import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  sha256Hex: vi.fn(async () => "a".repeat(64)),
  createUploadSlot: vi.fn(),
  sendFile: vi.fn(),
  completeUpload: vi.fn(),
  getDocument: vi.fn()
}));

vi.mock("@/lib/documents-api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/documents-api")>();
  return { ...real, ...api };
});

import { UploadDropzone } from "@/components/upload-dropzone";
import { DocumentsApiError, stageLabel } from "@/lib/documents-api";

function pick(file: File) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

const pdf = () => new File(["%PDF-1.4 hello"], "minutes.pdf", { type: "application/pdf" });
const doc = { id: "d1", document_id: "d1", title: "minutes.pdf", error_message: null };

describe("UploadDropzone", () => {
  beforeEach(() => {
    vi.useRealTimers();
    Object.values(api).forEach((fn) => fn.mockReset());
    api.sha256Hex.mockResolvedValue("a".repeat(64));
  });

  it("walks a file through upload, processing and ready", async () => {
    api.createUploadSlot.mockResolvedValue({
      document: doc, job: { stage: "awaiting_upload" }, duplicate: false,
      upload: { method: "PUT", url: "/documents/d1/content", requires_auth: true }
    });
    api.sendFile.mockImplementation(async (_t: unknown, _f: File, onProgress: (n: number) => void) => onProgress(0.5));
    api.completeUpload.mockResolvedValue({ document: doc, job: { stage: "queued" } });
    api.getDocument.mockResolvedValue({ document: { ...doc, status: "ready" }, job: { stage: "done" } });

    render(<UploadDropzone />);
    pick(pdf());
    await waitFor(() => expect(screen.getByRole("link", { name: "View in Documents" })).toBeTruthy(), {
      timeout: 5000
    });
    expect(api.createUploadSlot).toHaveBeenCalledWith(expect.any(File), "a".repeat(64), null);
    expect(api.sendFile).toHaveBeenCalled();
    expect(api.completeUpload).toHaveBeenCalledWith("d1");
  });

  it("says when the file is already in the library and sends nothing", async () => {
    api.createUploadSlot.mockResolvedValue({
      document: { ...doc, status: "ready" }, job: { stage: "done" }, duplicate: true, upload: null
    });
    render(<UploadDropzone />);
    pick(pdf());
    await waitFor(() => expect(screen.getByText("Already in your library")).toBeTruthy());
    expect(api.sendFile).not.toHaveBeenCalled();
  });

  it("says when the existing copy of the file had failed", async () => {
    api.createUploadSlot.mockResolvedValue({
      document: { ...doc, status: "failed", error_message: "No text was extracted from the uploaded file." },
      job: { stage: "failed" }, duplicate: true, upload: null
    });
    render(<UploadDropzone />);
    pick(pdf());
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Use Reprocess"));
    expect(api.sendFile).not.toHaveBeenCalled();
  });

  it("shows the server's reason when processing fails", async () => {
    api.createUploadSlot.mockResolvedValue({
      document: doc, job: { stage: "awaiting_upload" }, duplicate: false,
      upload: { method: "POST", url: "https://bucket.example/", fields: { key: "k" }, requires_auth: false }
    });
    api.sendFile.mockResolvedValue(undefined);
    api.completeUpload.mockResolvedValue({ document: doc, job: { stage: "queued" } });
    api.getDocument.mockResolvedValue({
      document: { ...doc, status: "failed", error_message: "No text was extracted from the uploaded file." },
      job: { stage: "failed" }
    });
    render(<UploadDropzone />);
    pick(pdf());
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("No text was extracted"), {
      timeout: 5000
    });
  });

  it("explains a plan limit without uploading", async () => {
    api.createUploadSlot.mockRejectedValue(new DocumentsApiError("You have reached this month's upload limit.", 402));
    render(<UploadDropzone />);
    pick(pdf());
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("upload limit"));
  });

  it("rejects unsupported files before contacting the server", () => {
    render(<UploadDropzone />);
    pick(new File(["x"], "notes.docx", { type: "application/msword" }));
    expect(screen.getByRole("alert").textContent).toContain("not supported");
    expect(api.createUploadSlot).not.toHaveBeenCalled();
  });
});

describe("documents-api", () => {
  it("names stages in plain words", () => {
    expect(stageLabel("embedding")).toBe("Indexing for search");
    expect(stageLabel("transcribing")).toBe("Transcribing audio");
  });

  it("posts the signed form fields and file to storage, without the API token", async () => {
    const sent: { method?: string; url?: string; body?: unknown; headers: Record<string, string> } = { headers: {} };
    class FakeXhr {
      status = 200;
      responseText = "{}";
      upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
      onload?: () => void;
      onerror?: () => void;
      open(method: string, url: string) {
        sent.method = method;
        sent.url = url;
      }
      setRequestHeader(name: string, value: string) {
        sent.headers[name] = value;
      }
      send(body: unknown) {
        sent.body = body;
        this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    window.localStorage.setItem("omni_auth_token", "tok");
    const progress: number[] = [];
    const { sendFile: realSendFile } = await vi.importActual<typeof import("@/lib/documents-api")>(
      "@/lib/documents-api"
    );
    await realSendFile(
      { method: "POST", url: "https://bucket.example/", fields: { key: "k", policy: "p" }, requires_auth: false },
      pdf(),
      (p) => progress.push(p)
    );
    expect([sent.method, sent.url]).toEqual(["POST", "https://bucket.example/"]);
    expect(sent.headers.Authorization).toBeUndefined();
    const form = sent.body as FormData;
    expect(form.get("policy")).toBe("p");
    expect((form.get("file") as File).name).toBe("minutes.pdf");
    expect(progress).toEqual([0.5, 1]);
    vi.unstubAllGlobals();
  });
});
