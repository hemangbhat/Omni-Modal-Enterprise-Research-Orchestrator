"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";
import {
  archiveDocument,
  deleteDocument,
  downloadDocument,
  formatBytes,
  reprocessDocument
} from "@/lib/documents-api";
import { withWorkspaceQuery } from "@/lib/workspace";

type DocStatus = "ready" | "processing" | "uploaded" | "failed";

type LiveDoc = {
  document_id: string;
  title: string;
  source_kind: string;
  chunk_count: number;
  status: DocStatus;
  // Present for documents uploaded through the durable flow.
  file_name?: string;
  size_bytes?: number;
  created_at?: number;
  error_message?: string | null;
  projects?: { id: string; name: string }[];
};

const PAGE_SIZE = 25;

function formatDate(epochSeconds?: number): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}

type EntityRecord = {
  id: string;
  label: string;
  value: string;
  confidence: number;
};

function statusToDisplay(status: DocStatus): "Indexed" | "Processing" | "Uploading" | "Failed" {
  const map: Record<DocStatus, "Indexed" | "Processing" | "Uploading" | "Failed"> = {
    ready: "Indexed",
    processing: "Processing",
    uploaded: "Uploading",
    failed: "Failed"
  };
  return map[status] ?? "Uploading";
}

function StatusPill({ status }: { status: DocStatus }) {
  const display = statusToDisplay(status);
  if (display === "Indexed") {
    return (
      <span className="inline-flex items-center gap-xs rounded-full border border-primary-container/20 bg-primary-container/10 px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-primary-fixed-dim">
        <span className="h-1.5 w-1.5 rounded-full bg-primary-fixed-dim" /> Indexed
      </span>
    );
  }
  if (display === "Processing") {
    return (
      <span className="inline-flex items-center gap-xs rounded-full border border-amber-500/30 bg-amber-500/10 px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-amber-400">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" /> Processing
      </span>
    );
  }
  if (display === "Uploading") {
    return (
      <span className="inline-flex items-center gap-xs rounded-full border border-outline-variant/30 bg-surface-variant px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-on-surface-variant">
        <span className="h-1.5 w-1.5 rounded-full bg-outline" /> Uploaded
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-xs rounded-full border border-error/30 bg-error-container/20 px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-error">
      <MaterialIcon name="error" size={10} /> Failed
    </span>
  );
}

function iconForKind(kind: string) {
  if (kind === "pdf") return { icon: "picture_as_pdf", wrap: "bg-error-container/20 text-error" };
  if (kind === "audio") return { icon: "mic", wrap: "bg-primary-container/10 text-primary-fixed-dim" };
  return { icon: "description", wrap: "bg-secondary-container/20 text-on-surface-variant" };
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<LiveDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [entities, setEntities] = useState<EntityRecord[]>([]);
  const [entitiesLoading, setEntitiesLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [sort, setSort] = useState("newest");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  // Seed filters from the address (?q= from the top-bar search, ?status= from the dashboard).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q");
    if (q) {
      setSearch(q);
      setQuery(q);
    }
    const status = params.get("status");
    if (status) setStatusFilter(status);
  }, []);

  // Search on the server as the user types, after a short pause.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const { baseUrl, token } = getClientApiConfig();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  const [actionError, setActionError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const params = new URLSearchParams({ sort, page: String(page), page_size: String(PAGE_SIZE) });
      if (query) params.set("q", query);
      if (statusFilter) params.set("status", statusFilter);
      if (kindFilter) params.set("kind", kindFilter);
      if (mine) params.set("mine", "true");
      const r = await apiRequest(withWorkspaceQuery(`/documents?${params}`), { method: "GET", headers }, { baseUrl });
      if (!r.ok) throw new Error(`The document list could not be loaded (HTTP ${r.status}).`);
      const data = (await r.json()) as { documents: LiveDoc[]; total?: number };
      setDocs(data.documents ?? []);
      setTotal(data.total ?? (data.documents ?? []).length);
      setError(null);
    } catch (err) {
      if (!quiet) setError(err instanceof Error ? err.message : "The document list could not be loaded.");
    } finally {
      if (!quiet) setLoading(false);
    }
    // headers/baseUrl come from the signed-in session and do not change while mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, statusFilter, kindFilter, sort, mine, page]);

  useEffect(() => {
    void load();
  }, [load]);

  // While anything is being processed, refresh quietly, for at most 15 minutes.
  // An upload slot nobody finished stays "uploaded"; after 30 minutes it no
  // longer counts as in progress (the server clears it after a day).
  const pending = docs.some(
    (d) =>
      d.status === "processing" ||
      (d.status === "uploaded" && (!d.created_at || Date.now() / 1000 - d.created_at < 30 * 60))
  );
  useEffect(() => {
    if (!pending) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - started > 15 * 60 * 1000) {
        window.clearInterval(timer);
        return;
      }
      void load(true);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [pending, load]);

  async function runAction(name: string, action: () => Promise<unknown>) {
    setBusyAction(name);
    setActionError(null);
    try {
      await action();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "That did not work. Try again.");
    } finally {
      setBusyAction(null);
    }
  }

  function selectDoc(doc: LiveDoc) {
    setConfirmDelete(false);
    setActionError(null);
    setSelectedId(doc.document_id);
    setInspectorOpen(true);
    setEntities([]);
    if (doc.status === "ready") {
      setEntitiesLoading(true);
      apiRequest(`/entities/${doc.document_id}`, { method: "GET", headers }, { baseUrl })
        .then((r) => r.json())
        .then((data: { entities: EntityRecord[] }) => setEntities(data.entities ?? []))
        .catch(() => setEntities([]))
        .finally(() => setEntitiesLoading(false));
    }
  }

  const selected = docs.find((d) => d.document_id === selectedId);
  const filtered = docs;
  const filtering = Boolean(query || statusFilter || kindFilter || mine);
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-surface">
      <div className="pointer-events-none absolute right-0 top-0 h-[600px] w-[600px] -translate-y-1/4 rounded-full bg-primary-container/5 blur-[120px]" />

      {/* Page header */}
      <div className="relative z-10 flex flex-col justify-between gap-md border-b border-outline-variant/10 bg-surface/40 px-xl pb-lg pt-xl backdrop-blur-sm sm:flex-row sm:items-end">
        <div>
          <div className="mb-xs flex items-center gap-sm text-on-surface-variant">
            <span className="font-mono-sm text-mono-sm">Workspace</span>
            <MaterialIcon name="chevron_right" size={14} />
            <span className="font-mono-sm text-mono-sm text-primary-fixed-dim">Document Library</span>
          </div>
          <h2 className="font-headline-lg text-headline-lg text-on-surface">Knowledge Base</h2>
          <p className="mt-sm max-w-2xl font-body-md text-body-md text-on-surface-variant">
            {loading
              ? "Loading documents…"
              : filtering
                ? `${total} matching document${total !== 1 ? "s" : ""}.`
                : `${total} document${total !== 1 ? "s" : ""} in your library. Archived documents are on the Archives page.`}
          </p>
        </div>
        <div className="flex items-center gap-md">
          <div className="relative">
            <MaterialIcon name="search" size={18} className="absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant" />
            <input
              type="text"
              aria-label="Search document titles and file names"
              placeholder="Search titles and file names…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-64 rounded-lg border border-outline-variant/30 bg-surface-container-lowest py-sm pl-xl pr-md font-body-md text-body-md text-on-surface shadow-[inset_0_2px_4px_rgba(0,0,0,0.2)] transition-all placeholder:text-on-surface-variant/50 focus:border-primary-fixed-dim focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim"
            />
          </div>
          <Link
            href="/upload"
            className="flex items-center gap-sm rounded-lg bg-primary-container px-md py-sm font-label-md text-label-md text-on-primary-container shadow-[0_0_10px_rgba(0,218,243,0.15)] transition-colors hover:bg-primary-fixed-dim"
          >
            <MaterialIcon name="upload_file" size={16} />
            Upload
          </Link>
        </div>
      </div>

      {/* Filters */}
      <div className="relative z-10 flex flex-wrap items-center gap-sm border-b border-outline-variant/10 px-xl py-sm text-[14px]">
        <label className="sr-only" htmlFor="filter-status">Status</label>
        <select
          id="filter-status"
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
          className="rounded-lg border border-outline-variant/40 bg-surface px-sm py-xs text-on-surface"
        >
          <option value="">Any status</option>
          <option value="ready">Ready</option>
          <option value="processing">Processing</option>
          <option value="uploaded">Waiting</option>
          <option value="failed">Failed</option>
        </select>
        <label className="sr-only" htmlFor="filter-kind">Type</label>
        <select
          id="filter-kind"
          value={kindFilter}
          onChange={(e) => {
            setKindFilter(e.target.value);
            setPage(1);
          }}
          className="rounded-lg border border-outline-variant/40 bg-surface px-sm py-xs text-on-surface"
        >
          <option value="">PDF and audio</option>
          <option value="pdf">PDF</option>
          <option value="audio">Audio</option>
        </select>
        <label className="sr-only" htmlFor="filter-sort">Sort</label>
        <select
          id="filter-sort"
          value={sort}
          onChange={(e) => {
            setSort(e.target.value);
            setPage(1);
          }}
          className="rounded-lg border border-outline-variant/40 bg-surface px-sm py-xs text-on-surface"
        >
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="title">Title A to Z</option>
          <option value="size">Largest first</option>
        </select>
        <label className="flex items-center gap-xs text-on-surface-variant">
          <input
            type="checkbox"
            checked={mine}
            onChange={(e) => {
              setMine(e.target.checked);
              setPage(1);
            }}
            className="accent-primary-fixed-dim"
          />
          Uploaded by me
        </label>
      </div>

      {/* Workspace */}
      <div className="relative z-10 flex flex-1 overflow-hidden">
        <div className="min-w-0 flex-1 overflow-y-auto px-xl py-lg">
          {error ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-error/20 bg-error-container/10 p-xl text-center">
              <MaterialIcon name="error_outline" size={40} className="mb-md text-error" />
              <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">Failed to load documents</h3>
              <p className="font-body-md text-body-md text-on-surface-variant">{error}</p>
              <button
                type="button"
                onClick={() => void load()}
                className="mt-md rounded-lg border border-outline-variant px-md py-sm text-[14px] text-primary"
              >
                Try again
              </button>
            </div>
          ) : loading ? (
            <div className="flex items-center justify-center py-xl">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
              <span className="ml-md font-body-md text-body-md text-on-surface-variant">Loading knowledge base…</span>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-outline-variant/30 p-xl text-center">
              <MaterialIcon name="inbox" size={48} className="mb-md text-on-surface-variant/40" />
              <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">
                {search ? "No matching documents" : "No documents yet"}
              </h3>
              <p className="mb-lg font-body-md text-body-md text-on-surface-variant">
                {search
                  ? `No documents match "${search}". Clear the search to see all documents.`
                  : "Upload PDF or audio files to build your knowledge base."}
              </p>
              {!search && (
                <Link
                  href="/upload"
                  className="flex items-center gap-sm rounded-lg bg-primary-container px-lg py-md font-label-md text-label-md text-on-primary-container transition-colors hover:bg-primary"
                >
                  <MaterialIcon name="cloud_upload" size={18} /> Upload your first document
                </Link>
              )}
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-outline-variant/20 bg-surface-container-low shadow-[0_4px_24px_rgba(0,0,0,0.2)]">
              <table className="w-full border-collapse text-left">
                <thead className="sticky top-0 z-20 bg-surface-container-lowest backdrop-blur-md">
                  <tr className="border-b border-outline-variant/20">
                    <th className="px-md py-sm font-label-md text-label-md font-medium text-on-surface-variant">Name</th>
                    <th className="px-md py-sm font-label-md text-label-md font-medium text-on-surface-variant">Type</th>
                    <th className="hidden px-md py-sm font-label-md text-label-md font-medium text-on-surface-variant md:table-cell">Size</th>
                    <th className="hidden px-md py-sm font-label-md text-label-md font-medium text-on-surface-variant md:table-cell">Added</th>
                    <th className="px-md py-sm font-label-md text-label-md font-medium text-on-surface-variant">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-outline-variant/10 font-body-md text-body-md text-on-surface">
                  {filtered.map((doc) => {
                    const active = doc.document_id === selectedId;
                    const { icon, wrap } = iconForKind(doc.source_kind);
                    return (
                      <tr
                        key={doc.document_id}
                        onClick={() => selectDoc(doc)}
                        className={`group cursor-pointer transition-colors ${
                          active
                            ? "border-l-2 border-primary-fixed-dim bg-surface-container-high/30"
                            : "border-l-2 border-transparent hover:bg-surface-container-highest/30"
                        }`}
                      >
                        <td className="px-md py-md">
                          <div className="flex items-center gap-md">
                            <div className={`flex h-8 w-8 items-center justify-center rounded ${wrap}`}>
                              <MaterialIcon name={icon} size={16} />
                            </div>
                            <div>
                              <div className="max-w-[260px] truncate font-medium text-on-surface">{doc.title}</div>
                              {doc.status === "failed" && doc.error_message ? (
                                <div className="max-w-[260px] truncate text-[12px] text-error" title={doc.error_message}>
                                  {doc.error_message}
                                </div>
                              ) : doc.file_name && doc.file_name !== doc.title ? (
                                <div className="max-w-[260px] truncate text-[12px] text-on-surface-variant/70">
                                  {doc.file_name}
                                </div>
                              ) : null}
                            </div>
                          </div>
                        </td>
                        <td className="px-md py-md capitalize text-on-surface-variant">{doc.source_kind}</td>
                        <td className="hidden px-md py-md text-[14px] text-on-surface-variant md:table-cell">
                          {doc.size_bytes ? formatBytes(doc.size_bytes) : "—"}
                        </td>
                        <td className="hidden px-md py-md text-[14px] text-on-surface-variant md:table-cell">
                          {formatDate(doc.created_at)}
                        </td>
                        <td className="px-md py-md">
                          <StatusPill status={doc.status} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {total > PAGE_SIZE ? (
                <nav aria-label="Pages" className="flex items-center justify-between border-t border-outline-variant/20 px-md py-sm text-[14px] text-on-surface-variant">
                  <span>
                    {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, total)} of {total}
                  </span>
                  <span className="flex gap-sm">
                    <button
                      type="button"
                      disabled={page <= 1}
                      onClick={() => setPage((n) => Math.max(1, n - 1))}
                      className="rounded-lg border border-outline-variant/40 px-md py-xs text-on-surface disabled:opacity-40"
                    >
                      Previous
                    </button>
                    <button
                      type="button"
                      disabled={page >= lastPage}
                      onClick={() => setPage((n) => Math.min(lastPage, n + 1))}
                      className="rounded-lg border border-outline-variant/40 px-md py-xs text-on-surface disabled:opacity-40"
                    >
                      Next
                    </button>
                  </span>
                </nav>
              ) : null}
            </div>
          )}
        </div>

        {/* Inspector */}
        {inspectorOpen && selected ? (
          <aside className="relative z-20 hidden w-[340px] flex-col border-l border-outline-variant/20 bg-surface-container-lowest/80 shadow-[-10px_0_30px_rgba(0,0,0,0.3)] backdrop-blur-xl lg:flex">
            <div className="flex items-start justify-between border-b border-outline-variant/10 p-lg">
              <div className="flex items-center gap-md">
                <div className={`flex h-10 w-10 items-center justify-center rounded-lg border border-outline-variant/20 ${iconForKind(selected.source_kind).wrap}`}>
                  <MaterialIcon name={iconForKind(selected.source_kind).icon} size={20} />
                </div>
                <div>
                  <h3 className="max-w-[200px] truncate text-base font-semibold text-on-surface" title={selected.title}>
                    {selected.title}
                  </h3>
                  <p className="mt-xs font-mono-sm text-mono-sm capitalize text-on-surface-variant">
                    {selected.source_kind} Document
                  </p>
                </div>
              </div>
              <button
                type="button"
                aria-label="Close document details"
                onClick={() => setInspectorOpen(false)}
                className="rounded p-1 text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface"
              >
                <MaterialIcon name="close" size={18} />
              </button>
            </div>

            <div className="flex flex-1 flex-col gap-xl overflow-y-auto p-lg">
              <div className="flex flex-col gap-sm">
                <Link
                  href="/research"
                  className="flex w-full items-center justify-center gap-sm rounded-lg bg-primary-fixed-dim py-md text-[14px] font-semibold text-on-primary-fixed transition-opacity hover:opacity-90"
                >
                  <MaterialIcon name="science" size={18} /> Ask about your documents
                </Link>
                {selected.size_bytes ? (
                  <div className="grid grid-cols-2 gap-sm">
                    <button
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => void runAction("download", () => downloadDocument(selected.document_id))}
                      className="flex items-center justify-center gap-xs rounded-lg border border-outline-variant/40 py-sm text-[13px] text-on-surface hover:bg-surface-container disabled:opacity-50"
                    >
                      <MaterialIcon name="download" size={16} />
                      {busyAction === "download" ? "Preparing…" : "Download"}
                    </button>
                    <button
                      type="button"
                      disabled={busyAction !== null || selected.status === "uploaded" || selected.status === "processing"}
                      onClick={() =>
                        void runAction("reprocess", async () => {
                          await reprocessDocument(selected.document_id);
                          await load(true);
                        })
                      }
                      className="flex items-center justify-center gap-xs rounded-lg border border-outline-variant/40 py-sm text-[13px] text-on-surface hover:bg-surface-container disabled:opacity-50"
                    >
                      <MaterialIcon name="refresh" size={16} />
                      {busyAction === "reprocess" ? "Starting…" : "Reprocess"}
                    </button>
                  </div>
                ) : null}
                {selected.size_bytes ? (
                  <button
                    type="button"
                    disabled={busyAction !== null}
                    onClick={() =>
                      void runAction("archive", async () => {
                        await archiveDocument(selected.document_id);
                        setInspectorOpen(false);
                        setSelectedId(null);
                        await load(true);
                      })
                    }
                    className="flex items-center justify-center gap-xs rounded-lg py-sm text-[13px] text-on-surface-variant hover:text-on-surface disabled:opacity-50"
                  >
                    <MaterialIcon name="inventory_2" size={16} />
                    {busyAction === "archive" ? "Archiving…" : "Archive (hide from research)"}
                  </button>
                ) : null}
                {confirmDelete ? (
                  <div className="rounded-lg border border-error/30 bg-error-container/10 p-sm text-[13px]">
                    <p className="mb-sm text-on-surface">
                      Delete this document, its search index and its file? This cannot be undone.
                    </p>
                    <div className="flex gap-sm">
                      <button
                        type="button"
                        disabled={busyAction !== null}
                        onClick={() =>
                          void runAction("delete", async () => {
                            await deleteDocument(selected.document_id);
                            setInspectorOpen(false);
                            setSelectedId(null);
                            setConfirmDelete(false);
                            await load(true);
                          })
                        }
                        className="rounded-md bg-error px-md py-xs font-semibold text-on-error disabled:opacity-50"
                      >
                        {busyAction === "delete" ? "Deleting…" : "Delete"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDelete(false)}
                        className="rounded-md px-md py-xs text-on-surface-variant"
                      >
                        Keep it
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(true)}
                    className="flex items-center justify-center gap-xs rounded-lg py-sm text-[13px] text-on-surface-variant hover:text-error"
                  >
                    <MaterialIcon name="delete" size={16} /> Delete document
                  </button>
                )}
                {actionError ? (
                  <p role="alert" className="text-[13px] text-error">
                    {actionError}
                  </p>
                ) : null}
              </div>

              <div>
                <h4 className="mb-md border-b border-outline-variant/10 pb-sm font-mono-sm text-mono-sm uppercase tracking-widest text-on-surface-variant">
                  Document Info
                </h4>
                <div className="flex flex-col gap-md">
                  {selected.file_name ? <MetaRow label="File" value={selected.file_name} /> : null}
                  <MetaRow label="Type" value={selected.source_kind.toUpperCase()} />
                  {selected.size_bytes ? <MetaRow label="Size" value={formatBytes(selected.size_bytes)} /> : null}
                  {selected.created_at ? <MetaRow label="Added" value={formatDate(selected.created_at)} /> : null}
                  <MetaRow label="Passages" value={String(selected.chunk_count)} />
                  {selected.projects && selected.projects.length > 0 ? (
                    <MetaRow label="Projects" value={selected.projects.map((pr) => pr.name).join(", ")} />
                  ) : null}
                  <div className="flex items-center justify-between">
                    <span className="font-label-md text-label-md text-on-surface-variant">Status</span>
                    <StatusPill status={selected.status} />
                  </div>
                  {selected.status === "failed" && selected.error_message ? (
                    <p className="rounded-lg bg-error-container/10 p-sm text-[13px] leading-snug text-error">
                      {selected.error_message}
                    </p>
                  ) : null}
                </div>
              </div>

              <div>
                <h4 className="mb-md flex items-center gap-xs border-b border-outline-variant/10 pb-sm font-mono-sm text-mono-sm uppercase tracking-widest text-on-surface-variant">
                  <MaterialIcon name="auto_awesome" size={14} className="text-primary-fixed-dim" />
                  Extracted Entities
                </h4>
                {entitiesLoading ? (
                  <div className="flex items-center gap-sm text-on-surface-variant">
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
                    <span className="font-mono-sm text-[11px]">Extracting…</span>
                  </div>
                ) : entities.length === 0 ? (
                  <p className="font-mono-sm text-[11px] text-on-surface-variant/60">
                    {selected.status === "ready"
                      ? "No entities extracted yet."
                      : "Document must be indexed before entity extraction runs."}
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-sm">
                    {entities.slice(0, 12).map((ent) => (
                      <span
                        key={ent.id}
                        title={`${ent.label} — confidence: ${(ent.confidence * 100).toFixed(0)}%`}
                        className="rounded border border-outline-variant/20 bg-surface-container px-sm py-1 font-mono-sm text-[11px] text-on-surface-variant"
                      >
                        {ent.value}
                      </span>
                    ))}
                    {entities.length > 12 && (
                      <span className="rounded border border-dashed border-outline-variant/40 px-sm py-1 font-mono-sm text-[11px] text-on-surface-variant">
                        +{entities.length - 12} more
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
          </aside>
        ) : null}
      </div>
    </main>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="font-label-md text-label-md text-on-surface-variant">{label}</span>
      <span className="max-w-[180px] truncate text-right text-[13px] text-on-surface" title={value}>
        {value}
      </span>
    </div>
  );
}
