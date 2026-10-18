"use client";

import { useCallback, useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import { apiRequest } from "@/lib/api-client";
import { formatBytes, restoreDocument, type DocumentRecord } from "@/lib/documents-api";
import { getClientApiConfig } from "@/lib/env";
import { withWorkspaceQuery } from "@/lib/workspace";

type ArchivedDoc = DocumentRecord & { projects?: { id: string; name: string }[] };

function day(epochSeconds?: number | null): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}

export default function ArchivesPage() {
  const [docs, setDocs] = useState<ArchivedDoc[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [restoring, setRestoring] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    const { baseUrl, token } = getClientApiConfig();
    const params = new URLSearchParams({ page_size: "100" });
    if (query) params.set("q", query);
    if (kind) params.set("kind", kind);
    try {
      const r = await apiRequest(
        withWorkspaceQuery(`/archives?${params}`),
        { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        { baseUrl }
      );
      if (!r.ok) throw new Error(`The archive could not be loaded (HTTP ${r.status}).`);
      const data = (await r.json()) as { documents: ArchivedDoc[]; total: number };
      setDocs(data.documents ?? []);
      setTotal(data.total ?? 0);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The archive could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [query, kind]);

  useEffect(() => {
    void load();
  }, [load]);

  async function restore(doc: ArchivedDoc) {
    setRestoring(doc.id);
    setActionError(null);
    try {
      await restoreDocument(doc.id);
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "The document could not be restored.");
    } finally {
      setRestoring(null);
    }
  }

  return (
    <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-surface">
      <TopBar searchPlaceholder="Search documents, projects, research..." />
      <div className="flex-1 overflow-y-auto px-lg py-xl md:px-xl">
        <div className="mx-auto flex max-w-5xl flex-col gap-lg">
          <div>
            <h2 className="text-[30px] font-semibold tracking-tight text-on-surface">Archives</h2>
            <p className="mt-xs max-w-2xl text-[16px] leading-relaxed text-on-surface-variant">
              Archived documents are kept with their file and index, but research leaves them out until they are
              restored. Archive a document from the Documents page.
            </p>
          </div>

          <div className="flex flex-wrap gap-sm">
            <label className="sr-only" htmlFor="archive-search">Search archived documents</label>
            <input
              id="archive-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search titles and file names…"
              className="min-w-[240px] flex-1 rounded-lg border border-outline-variant/40 bg-surface px-md py-sm text-[14px] text-on-surface focus:border-primary-fixed-dim focus:outline-none"
            />
            <label className="sr-only" htmlFor="archive-kind">Type</label>
            <select
              id="archive-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              className="rounded-lg border border-outline-variant/40 bg-surface px-sm py-sm text-[14px] text-on-surface"
            >
              <option value="">PDF and audio</option>
              <option value="pdf">PDF</option>
              <option value="audio">Audio</option>
            </select>
          </div>

          {actionError ? <p role="alert" className="text-[14px] text-error">{actionError}</p> : null}

          {error ? (
            <p role="alert" className="text-error">{error}</p>
          ) : loading ? (
            <p className="text-on-surface-variant">Loading archive…</p>
          ) : docs.length === 0 ? (
            <div className="flex flex-col items-start gap-xs rounded-2xl border border-dashed border-outline-variant/40 p-lg">
              <MaterialIcon name="inventory_2" size={28} className="text-on-surface-variant/60" />
              <p className="text-[15px] text-on-surface">{query || kind ? "No archived documents match." : "Nothing is archived."}</p>
            </div>
          ) : (
            <>
              <p className="text-[14px] text-on-surface-variant">{total} archived document{total === 1 ? "" : "s"}</p>
              <ul className="divide-y divide-outline-variant/20 rounded-2xl border border-outline-variant/30">
                {docs.map((d) => (
                  <li key={d.id} className="flex flex-col gap-sm px-md py-md sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <p className="truncate text-[15px] font-medium text-on-surface">{d.title}</p>
                      <p className="text-[13px] text-on-surface-variant">
                        {d.source_kind.toUpperCase()}, {formatBytes(d.size_bytes)}, archived {day(d.archived_at)}
                        {d.projects && d.projects.length ? `, in ${d.projects.map((p) => p.name).join(", ")}` : ""}
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={restoring === d.id}
                      onClick={() => void restore(d)}
                      className="shrink-0 rounded-lg border border-outline-variant/40 px-md py-xs text-[14px] text-on-surface hover:bg-surface-container disabled:opacity-50"
                    >
                      {restoring === d.id ? "Restoring…" : "Restore"}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
