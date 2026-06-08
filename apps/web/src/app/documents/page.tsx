"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

type DocStatus = "ready" | "processing" | "uploaded" | "failed";

type LiveDoc = {
  document_id: string;
  title: string;
  source_kind: string;
  chunk_count: number;
  status: DocStatus;
};

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
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const { baseUrl, token } = getClientApiConfig();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  useEffect(() => {
    setLoading(true);
    apiRequest("/documents", { method: "GET", headers }, { baseUrl })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: { documents: LiveDoc[] }) => {
        setDocs(data.documents ?? []);
        setError(null);
      })
      .catch((err: Error) => {
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }, []);

  function selectDoc(doc: LiveDoc) {
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
  const filtered = docs.filter((d) =>
    !search || d.title.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <main className="relative flex flex-1 flex-col overflow-hidden bg-surface">
      <div className="pointer-events-none absolute -right-[10%] -top-[20%] h-[600px] w-[600px] rounded-full bg-primary-container/5 blur-[120px]" />

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
              : `${filtered.length} document${filtered.length !== 1 ? "s" : ""} in your knowledge base.`}
          </p>
        </div>
        <div className="flex items-center gap-md">
          <div className="relative">
            <MaterialIcon name="search" size={18} className="absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant" />
            <input
              type="text"
              placeholder="Search documents…"
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

      {/* Workspace */}
      <div className="relative z-10 flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-y-auto px-xl py-lg">
          {error ? (
            <div className="flex flex-col items-center justify-center rounded-xl border border-error/20 bg-error-container/10 p-xl text-center">
              <MaterialIcon name="error_outline" size={40} className="mb-md text-error" />
              <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">Failed to load documents</h3>
              <p className="font-body-md text-body-md text-on-surface-variant">{error}</p>
              <p className="mt-sm font-mono-sm text-mono-sm text-on-surface-variant">
                Ensure the backend is running and NEXT_PUBLIC_API_TOKEN is set.
              </p>
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
                    <th className="px-md py-sm font-label-md text-label-md font-medium text-on-surface-variant">Chunks</th>
                    <th className="px-md py-sm font-label-md text-label-md font-medium text-on-surface-variant">Status</th>
                    <th className="w-10 px-md py-sm" />
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
                              <div className="font-mono-sm text-mono-sm text-on-surface-variant/60 truncate max-w-[260px]">
                                ID: {doc.document_id.slice(0, 8)}…
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="px-md py-md capitalize text-on-surface-variant">{doc.source_kind}</td>
                        <td className="px-md py-md font-mono-sm text-on-surface-variant">{doc.chunk_count}</td>
                        <td className="px-md py-md">
                          <StatusPill status={doc.status} />
                        </td>
                        <td className="px-md py-md text-right">
                          <button className="text-on-surface-variant opacity-0 transition-all hover:text-on-surface group-hover:opacity-100">
                            <MaterialIcon name="more_vert" size={18} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
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
                onClick={() => setInspectorOpen(false)}
                className="rounded p-1 text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface"
              >
                <MaterialIcon name="close" size={18} />
              </button>
            </div>

            <div className="flex flex-1 flex-col gap-xl overflow-y-auto p-lg">
              <div className="flex flex-col gap-sm">
                <Link
                  href={`/research?document_id=${selected.document_id}`}
                  className="flex w-full items-center justify-center gap-sm rounded-lg bg-gradient-to-r from-primary-fixed-dim to-primary-container py-md font-label-md text-label-md text-on-primary-container shadow-[0_4px_14px_rgba(0,218,243,0.2)] transition-all hover:shadow-[0_6px_20px_rgba(0,218,243,0.3)]"
                >
                  <MaterialIcon name="science" size={18} /> Open in Research
                </Link>
              </div>

              <div>
                <h4 className="mb-md border-b border-outline-variant/10 pb-sm font-mono-sm text-mono-sm uppercase tracking-widest text-on-surface-variant">
                  Document Info
                </h4>
                <div className="flex flex-col gap-md">
                  <MetaRow label="Document ID" value={selected.document_id.slice(0, 16) + "…"} />
                  <MetaRow label="Type" value={selected.source_kind.toUpperCase()} />
                  <MetaRow label="Chunks" value={String(selected.chunk_count)} />
                  <div className="flex items-center justify-between">
                    <span className="font-label-md text-label-md text-on-surface-variant">Status</span>
                    <StatusPill status={selected.status} />
                  </div>
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
                      <span className="cursor-pointer rounded border border-dashed border-outline-variant/40 px-sm py-1 font-mono-sm text-[11px] text-on-surface-variant hover:text-on-surface">
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
      <span className="font-mono-sm text-mono-sm text-on-surface">{value}</span>
    </div>
  );
}
