"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";
import { withWorkspaceQuery } from "@/lib/workspace";

type LiveArchive = {
  id: string;
  name: string;
  title: string;
  source_kind: string;
  chunk_count: number;
  status: string;
};

export default function ArchivesPage() {
  const [archives, setArchives] = useState<LiveArchive[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const { baseUrl, token } = getClientApiConfig();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  useEffect(() => {
    setLoading(true);
    apiRequest(withWorkspaceQuery("/archives"), { method: "GET", headers }, { baseUrl })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: { archives: LiveArchive[] }) => {
        setArchives(data.archives ?? []);
        if (data.archives?.length > 0) setSelectedId(data.archives[0].id);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const selected = archives.find((a) => a.id === selectedId);
  const filtered = archives.filter(
    (a) => !search || a.title.toLowerCase().includes(search.toLowerCase()) || a.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <main className="relative flex flex-1 overflow-hidden bg-background">
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar searchPlaceholder="Search archives..." />
        <div className="relative flex-1 overflow-y-auto p-xl">
          <div className="pointer-events-none absolute left-1/4 top-0 h-[400px] w-[800px] rounded-full bg-primary/5 blur-[120px]" />
          <div className="relative z-10 mx-auto flex max-w-max_width flex-col gap-xl">

            {/* Header */}
            <div className="flex flex-col items-start justify-between gap-md lg:flex-row">
              <div className="space-y-1">
                <h1 className="flex items-center gap-md font-headline-lg text-headline-lg font-bold tracking-tight text-on-background">
                  <span className="rounded-xl bg-primary/10 p-2 text-primary-fixed-dim">
                    <MaterialIcon name="inventory_2" size={32} />
                  </span>
                  Archives
                </h1>
                <p className="font-body-md text-body-md text-on-surface-variant/80">
                  {loading
                    ? "Loading archives…"
                    : `${filtered.length} indexed document${filtered.length !== 1 ? "s" : ""}.`}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-md">
                <div className="relative">
                  <MaterialIcon name="search" size={16} className="absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant" />
                  <input
                    type="text"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search archives…"
                    className="rounded-full border border-outline-variant/30 bg-surface-container py-1.5 pl-10 pr-4 font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/50"
                  />
                </div>
              </div>
            </div>

            {/* Content */}
            {error ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-error/20 bg-error-container/10 p-xl text-center">
                <MaterialIcon name="error_outline" size={40} className="mb-md text-error" />
                <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">Failed to load archives</h3>
                <p className="font-body-md text-body-md text-on-surface-variant">{error}</p>
              </div>
            ) : loading ? (
              <div className="flex items-center justify-center py-xl">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
                <span className="ml-md font-body-md text-body-md text-on-surface-variant">Loading documents…</span>
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-outline-variant/30 p-xl text-center">
                <MaterialIcon name="inventory_2" size={48} className="mb-md text-on-surface-variant/40" />
                <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">
                  {search ? "No matching archives" : "No archives yet"}
                </h3>
                <p className="mb-lg font-body-md text-body-md text-on-surface-variant">
                  {search
                    ? `No archives match "${search}".`
                    : "Indexed documents are listed here."}
                </p>
                {!search && (
                  <Link
                    href="/upload"
                    className="flex items-center gap-sm rounded-lg bg-primary-container px-lg py-md font-label-md text-label-md text-on-primary-container transition-colors hover:bg-primary"
                  >
                    <MaterialIcon name="cloud_upload" size={18} /> Upload documents
                  </Link>
                )}
              </div>
            ) : (
              <div className="relative flex flex-1 flex-col overflow-hidden rounded-xl border border-outline-variant/20 bg-surface-container shadow-2xl">
                <div className="flex-1 overflow-x-auto">
                  <table className="w-full min-w-[700px] border-collapse whitespace-nowrap text-left">
                    <thead className="sticky top-0 z-20 border-b border-outline-variant/30 bg-surface-container-high/50 backdrop-blur-md">
                      <tr>
                        <th className="px-md py-sm font-label-md text-label-md font-semibold text-on-surface-variant">Archive Name</th>
                        <th className="px-md py-sm font-label-md text-label-md font-semibold text-on-surface-variant">Title</th>
                        <th className="px-md py-sm font-label-md text-label-md font-semibold text-on-surface-variant">Type</th>
                        <th className="px-md py-sm font-label-md text-label-md font-semibold text-on-surface-variant">Chunks</th>
                        <th className="px-md py-sm font-label-md text-label-md font-semibold text-on-surface-variant">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-outline-variant/10 font-body-md text-body-md text-on-surface">
                      {filtered.map((archive) => {
                        const active = archive.id === selectedId;
                        return (
                          <tr
                            key={archive.id}
                            onClick={() => setSelectedId(archive.id)}
                            className={`group cursor-pointer transition-all hover:bg-surface-container-high/50 ${active ? "bg-primary/5" : ""}`}
                          >
                            <td className="px-md py-2.5">
                              <div className="flex items-center gap-3">
                                <MaterialIcon name="folder_zip" size={20} className={active ? "text-primary" : "text-on-surface-variant/40"} />
                                <div>
                                  <div className="text-sm font-medium text-on-surface">{archive.name}</div>
                                  <div className="text-[10px] uppercase tracking-widest text-on-surface-variant/60">{archive.source_kind}</div>
                                </div>
                              </div>
                            </td>
                            <td className="px-md py-2.5 text-sm text-on-surface-variant max-w-[180px]">
                              <span className="block truncate" title={archive.title}>{archive.title}</span>
                            </td>
                            <td className="px-md py-2.5 text-sm uppercase text-on-surface-variant">{archive.source_kind}</td>
                            <td className="px-md py-2.5 font-mono-sm text-sm text-on-surface-variant">{archive.chunk_count}</td>
                            <td className="px-md py-2.5">
                              <span className="inline-flex items-center gap-1 rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                                <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                                {archive.status}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="flex items-center justify-between border-t border-outline-variant/30 bg-surface-container-high px-md py-sm">
                  <span className="font-label-md text-[12px] text-on-surface-variant">
                    Showing {filtered.length} of {archives.length} records
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Inspector */}
      {selected ? (
        <aside className="z-30 hidden w-[380px] flex-shrink-0 flex-col border-l border-outline-variant/30 bg-surface-container-high shadow-2xl xl:flex">
          <div className="flex items-center justify-between border-b border-outline-variant/20 bg-surface-dim p-lg">
            <h2 className="flex items-center gap-2 font-headline-md text-on-surface">
              <MaterialIcon name="info" className="text-primary" />
              Document Inspector
            </h2>
            <button
              type="button"
              aria-label="Close inspector"
              onClick={() => setSelectedId(null)}
              className="text-on-surface-variant hover:text-primary"
            >
              <MaterialIcon name="close" />
            </button>
          </div>
          <div className="flex-1 space-y-lg overflow-y-auto p-lg">
            <div className="rounded-xl border border-outline-variant/20 bg-surface-container-lowest p-md">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-md">
                  <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10">
                    <MaterialIcon name="folder_zip" size={28} className="text-primary" />
                  </div>
                  <div>
                    <div className="text-base font-bold text-on-surface">{selected.name}</div>
                    <div className="mt-0.5 text-xs text-on-surface-variant truncate max-w-[160px]" title={selected.title}>
                      {selected.title}
                    </div>
                  </div>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-outline-variant/20 pt-4 text-sm">
                <div className="text-xs uppercase tracking-wider text-on-surface-variant">Status</div>
                <div className="flex items-center gap-1 font-medium text-primary">
                  <span className="inline-block h-2 w-2 rounded-full bg-primary" /> {selected.status}
                </div>
                <div className="text-xs uppercase tracking-wider text-on-surface-variant">Type</div>
                <div className="font-mono-sm uppercase text-on-surface">{selected.source_kind}</div>
                <div className="text-xs uppercase tracking-wider text-on-surface-variant">Chunks indexed</div>
                <div className="font-mono-sm text-on-surface">{selected.chunk_count}</div>
              </div>
            </div>
            <div className="flex flex-col gap-sm">
              <Link
                href="/research"
                className="flex w-full items-center justify-center gap-sm rounded-lg bg-primary py-md font-label-md text-label-md text-on-primary transition-colors hover:bg-primary-container"
              >
                <MaterialIcon name="science" size={18} /> Ask about this document
              </Link>
              <Link
                href="/documents"
                className="flex w-full items-center justify-center gap-sm rounded-lg border border-outline-variant/30 py-md font-label-md text-label-md text-on-surface transition-colors hover:bg-surface-container-high"
              >
                <MaterialIcon name="description" size={18} /> View in Documents
              </Link>
            </div>
          </div>
        </aside>
      ) : null}
    </main>
  );
}
