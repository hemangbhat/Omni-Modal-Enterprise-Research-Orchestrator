"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

type ProjectStatus = "Active" | "Completed";

type LiveProject = {
  id: string;
  code: string;
  name: string;
  icon: string;
  status: ProjectStatus;
  source_kind: string;
  chunk_count: number;
  updated: string;
  docs: number;
};

function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  return status === "Active" ? (
    <span className="inline-flex items-center rounded border border-primary/20 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
      Active
    </span>
  ) : (
    <span className="inline-flex items-center rounded border border-outline-variant bg-surface-bright px-2 py-0.5 text-xs font-medium text-on-surface-variant">
      Completed
    </span>
  );
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<LiveProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const { baseUrl, token } = getClientApiConfig();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  useEffect(() => {
    setLoading(true);
    apiRequest("/projects", { method: "GET", headers }, { baseUrl })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: { projects: LiveProject[] }) => {
        setProjects(data.projects ?? []);
        if (data.projects?.length > 0) {
          setSelectedId(data.projects[0].id);
          setOpen(true);
        }
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const selected = projects.find((p) => p.id === selectedId);
  const filtered = projects.filter(
    (p) => !search || p.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <main className="relative flex flex-1 overflow-hidden bg-surface">
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar searchPlaceholder="Search across workspace..." />
        <div className="flex-1 overflow-y-auto px-xl py-lg">
          <div className="mx-auto flex w-full max-w-[1600px] flex-col">

            {/* Header */}
            <div className="mb-lg flex flex-col justify-between gap-md sm:flex-row sm:items-end">
              <div>
                <h2 className="mb-xs font-headline-lg text-headline-lg text-on-surface">
                  Projects Directory
                </h2>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  {loading ? "Loading projects…" : `${filtered.length} research project${filtered.length !== 1 ? "s" : ""} in your workspace.`}
                </p>
              </div>
              <div className="flex items-center gap-md">
                <div className="relative">
                  <MaterialIcon name="search" size={16} className="absolute left-sm top-1/2 -translate-y-1/2 text-on-surface-variant" />
                  <input
                    type="text"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search projects…"
                    className="rounded border border-outline-variant bg-surface-container py-xs pl-xl pr-md font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/50"
                  />
                </div>
                <Link
                  href="/upload"
                  className="flex items-center gap-sm rounded border border-primary-fixed-dim bg-primary px-md py-[8px] font-label-md text-label-md font-medium text-on-primary transition-all hover:bg-primary-fixed hover:shadow-sm"
                >
                  <MaterialIcon name="add" size={18} />
                  New Project
                </Link>
              </div>
            </div>

            {/* Content */}
            {error ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-error/20 bg-error-container/10 p-xl text-center">
                <MaterialIcon name="error_outline" size={40} className="mb-md text-error" />
                <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">Failed to load projects</h3>
                <p className="font-body-md text-body-md text-on-surface-variant">{error}</p>
              </div>
            ) : loading ? (
              <div className="flex items-center justify-center py-xl">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
                <span className="ml-md font-body-md text-body-md text-on-surface-variant">Loading workspace…</span>
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-outline-variant/30 p-xl text-center">
                <MaterialIcon name="folder_open" size={48} className="mb-md text-on-surface-variant/40" />
                <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">
                  {search ? "No matching projects" : "No projects yet"}
                </h3>
                <p className="mb-lg font-body-md text-body-md text-on-surface-variant">
                  {search
                    ? `No projects match "${search}".`
                    : "Upload documents to create research projects. Each indexed document becomes a project."}
                </p>
                {!search && (
                  <Link
                    href="/upload"
                    className="flex items-center gap-sm rounded-lg bg-primary-container px-lg py-md font-label-md text-label-md text-on-primary-container transition-colors hover:bg-primary"
                  >
                    <MaterialIcon name="cloud_upload" size={18} /> Upload first document
                  </Link>
                )}
              </div>
            ) : (
              <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-low">
                <div className="flex-1 overflow-x-auto">
                  <table className="w-full min-w-[700px] border-collapse whitespace-nowrap text-left">
                    <thead>
                      <tr className="border-b border-outline-variant font-label-md text-label-md text-on-surface-variant">
                        <th className="px-md py-sm font-medium">Project Name</th>
                        <th className="px-md py-sm font-medium">Status</th>
                        <th className="px-md py-sm font-medium">Type</th>
                        <th className="px-md py-sm font-medium">Chunks</th>
                        <th className="px-md py-sm font-medium">Last Updated</th>
                        <th className="w-16 px-md py-sm text-right" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-outline-variant/50 font-body-md text-body-md text-on-surface">
                      {filtered.map((project) => {
                        const active = project.id === selectedId;
                        return (
                          <tr
                            key={project.id}
                            onClick={() => { setSelectedId(project.id); setOpen(true); }}
                            className={`cursor-pointer transition-colors hover:bg-surface-container-highest ${
                              active ? "border-l-2 border-l-primary bg-primary/5" : "border-l-2 border-l-transparent"
                            }`}
                          >
                            <td className="px-md py-sm">
                              <div className="flex items-center gap-sm">
                                <div className="flex h-6 w-6 items-center justify-center rounded border border-outline-variant bg-surface-bright text-primary">
                                  <MaterialIcon name={project.icon} size={14} />
                                </div>
                                <div>
                                  <div className={`font-medium ${active ? "text-primary" : "text-on-surface"}`}>
                                    {project.name}
                                  </div>
                                  <div className="font-mono-sm text-[10px] text-on-surface-variant/60">{project.code}</div>
                                </div>
                              </div>
                            </td>
                            <td className="px-md py-sm">
                              <ProjectStatusBadge status={project.status} />
                            </td>
                            <td className="px-md py-sm capitalize text-on-surface-variant">{project.source_kind}</td>
                            <td className="px-md py-sm font-mono-sm text-on-surface-variant">{project.chunk_count}</td>
                            <td className="px-md py-sm font-mono-sm text-sm text-on-surface-variant">{project.updated}</td>
                            <td className="px-md py-sm text-right">
                              <button className="rounded p-1 text-on-surface-variant transition-colors hover:bg-surface-bright hover:text-on-surface">
                                <MaterialIcon name="more_vert" size={18} />
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="flex items-center justify-between border-t border-outline-variant bg-surface-container-high px-md py-sm text-sm text-on-surface-variant">
                  <span>Showing {filtered.length} of {projects.length} projects</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Detail drawer */}
      {open && selected ? (
        <aside className="z-20 hidden w-[380px] flex-shrink-0 flex-col border-l border-outline-variant bg-surface-container xl:flex">
          <header className="flex items-start justify-between border-b border-outline-variant bg-surface-container-high p-lg">
            <div>
              <div className="mb-xs flex items-center gap-sm">
                <ProjectStatusBadge status={selected.status} />
              </div>
              <h3 className="mb-xs font-headline-md text-headline-md font-semibold text-on-surface">
                {selected.name}
              </h3>
              <p className="text-sm text-on-surface-variant">ID: {selected.code}</p>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="rounded p-1 text-on-surface-variant transition-colors hover:bg-surface-bright hover:text-on-surface"
            >
              <MaterialIcon name="close" />
            </button>
          </header>
          <div className="flex-1 space-y-xl overflow-y-auto p-lg">
            <div className="grid grid-cols-2 gap-sm">
              <Link
                href={`/research`}
                className="flex items-center justify-center gap-xs rounded bg-primary py-2 font-label-md font-medium text-on-primary transition-colors hover:bg-primary-fixed"
              >
                <MaterialIcon name="science" size={18} /> Open Research
              </Link>
              <Link
                href={`/documents`}
                className="flex items-center justify-center gap-xs rounded border border-outline-variant bg-surface-bright py-2 font-label-md font-medium text-on-surface transition-colors hover:bg-surface-container-highest"
              >
                <MaterialIcon name="description" size={18} /> View Docs
              </Link>
            </div>
            <div>
              <h4 className="mb-md border-b border-outline-variant/50 pb-xs font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                Metadata
              </h4>
              <div className="space-y-sm text-sm">
                <div className="flex justify-between">
                  <span className="text-on-surface-variant">Document ID</span>
                  <span className="font-mono-sm text-on-surface">{selected.id.slice(0, 12)}…</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-on-surface-variant">Type</span>
                  <span className="font-mono-sm capitalize text-on-surface">{selected.source_kind}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-on-surface-variant">Chunks indexed</span>
                  <span className="font-mono-sm text-on-surface">{selected.chunk_count}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-on-surface-variant">Last updated</span>
                  <span className="font-mono-sm text-on-surface">{selected.updated}</span>
                </div>
              </div>
            </div>
          </div>
        </aside>
      ) : null}
    </main>
  );
}
