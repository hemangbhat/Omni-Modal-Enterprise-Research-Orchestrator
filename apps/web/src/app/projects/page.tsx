"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import { createProject, listProjects, type ProjectListEntry } from "@/lib/projects-api";

function updatedText(epochSeconds: number): string {
  const days = Math.floor((Date.now() / 1000 - epochSeconds) / 86400);
  if (days <= 0) return "Updated today";
  if (days === 1) return "Updated yesterday";
  if (days < 30) return `Updated ${days} days ago`;
  return `Updated ${new Date(epochSeconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}`;
}

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectListEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setProjects(await listProjects(showArchived));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Projects could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [showArchived]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit() {
    if (!name.trim() || saving) return;
    setSaving(true);
    setFormError(null);
    try {
      const project = await createProject(name.trim(), description.trim());
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "The project could not be created.");
      setSaving(false);
    }
  }

  return (
    <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-surface">
      <TopBar searchPlaceholder="Search documents, projects, research..." />
      <div className="flex-1 overflow-y-auto px-lg py-xl md:px-xl">
        <div className="mx-auto flex max-w-6xl flex-col gap-xl">
          <div className="flex flex-col justify-between gap-md sm:flex-row sm:items-end">
            <div>
              <h2 className="text-[30px] font-semibold tracking-tight text-on-surface">Projects</h2>
              <p className="mt-xs max-w-2xl text-[16px] leading-relaxed text-on-surface-variant">
                A project gathers the documents, research, findings and reports for one piece of work. Research
                started in a project only searches that project&apos;s documents.
              </p>
            </div>
            <div className="flex items-center gap-md">
              <label className="flex items-center gap-xs text-[14px] text-on-surface-variant">
                <input
                  type="checkbox"
                  checked={showArchived}
                  onChange={(e) => setShowArchived(e.target.checked)}
                  className="accent-primary-fixed-dim"
                />
                Show archived
              </label>
              <button
                type="button"
                onClick={() => setCreating((v) => !v)}
                aria-expanded={creating}
                className="flex items-center gap-xs rounded-lg bg-primary-fixed-dim px-md py-sm text-[14px] font-semibold text-on-primary-fixed hover:opacity-90"
              >
                <MaterialIcon name="add" size={18} />
                New project
              </button>
            </div>
          </div>

          {creating ? (
            <form
              className="flex flex-col gap-md rounded-2xl border border-outline-variant/30 bg-surface-container-low p-lg"
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              <label className="flex flex-col gap-xs text-[14px] text-on-surface-variant">
                Name
                <input
                  autoFocus
                  value={name}
                  maxLength={120}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Q3 board review"
                  className="rounded-lg border border-outline-variant/40 bg-surface px-md py-sm text-[15px] text-on-surface focus:border-primary-fixed-dim focus:outline-none"
                />
              </label>
              <label className="flex flex-col gap-xs text-[14px] text-on-surface-variant">
                What is it for? (optional)
                <textarea
                  value={description}
                  rows={2}
                  maxLength={2000}
                  onChange={(e) => setDescription(e.target.value)}
                  className="resize-none rounded-lg border border-outline-variant/40 bg-surface px-md py-sm text-[15px] text-on-surface focus:border-primary-fixed-dim focus:outline-none"
                />
              </label>
              {formError ? <p role="alert" className="text-[14px] text-error">{formError}</p> : null}
              <div className="flex gap-sm">
                <button
                  type="submit"
                  disabled={!name.trim() || saving}
                  className="rounded-lg bg-primary-fixed-dim px-lg py-sm text-[14px] font-semibold text-on-primary-fixed disabled:opacity-50"
                >
                  {saving ? "Creating…" : "Create project"}
                </button>
                <button type="button" onClick={() => setCreating(false)} className="px-md text-[14px] text-on-surface-variant">
                  Cancel
                </button>
              </div>
            </form>
          ) : null}

          {error ? (
            <div role="alert" className="rounded-2xl border border-error/30 bg-error-container/10 p-lg text-error">
              {error}{" "}
              <button type="button" onClick={() => void load()} className="underline">
                Try again
              </button>
            </div>
          ) : loading ? (
            <p className="text-on-surface-variant">Loading projects…</p>
          ) : projects.length === 0 ? (
            <div className="flex flex-col items-start gap-md rounded-2xl border border-dashed border-outline-variant/40 p-xl">
              <h3 className="text-[19px] font-semibold text-on-surface">No projects yet</h3>
              <p className="max-w-xl text-[15px] leading-relaxed text-on-surface-variant">
                Create a project, add the documents it is about, then research inside it and save the answers worth
                keeping as findings. A report can be generated from them at any time.
              </p>
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="rounded-lg border border-outline-variant px-md py-sm text-[14px] text-primary"
              >
                Create your first project
              </button>
            </div>
          ) : (
            <ul className="grid grid-cols-1 gap-md md:grid-cols-2 xl:grid-cols-3">
              {projects.map((p) => (
                <li key={p.id}>
                  <Link
                    href={`/projects/${p.id}`}
                    className="flex h-full flex-col gap-md rounded-2xl border border-outline-variant/30 bg-surface-container-low p-lg transition-colors hover:border-primary-fixed-dim/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-fixed-dim"
                  >
                    <div className="flex items-start justify-between gap-sm">
                      <h3 className="text-[17px] font-semibold leading-snug text-on-surface">{p.name}</h3>
                      {p.archived ? (
                        <span className="shrink-0 rounded-full bg-surface-container-highest px-sm py-[2px] text-[12px] text-on-surface-variant">
                          Archived
                        </span>
                      ) : null}
                    </div>
                    {p.description ? (
                      <p className="line-clamp-2 text-[14px] leading-relaxed text-on-surface-variant">{p.description}</p>
                    ) : null}
                    <dl className="mt-auto grid grid-cols-4 gap-sm border-t border-outline-variant/20 pt-md">
                      {(
                        [
                          ["Documents", p.counts.documents],
                          ["Findings", p.counts.findings],
                          ["Notes", p.counts.notes],
                          ["Reports", p.counts.reports]
                        ] as const
                      ).map(([label, value]) => (
                        <div key={label}>
                          <dt className="text-[12px] text-on-surface-variant/80">{label}</dt>
                          <dd className="text-[17px] font-semibold text-on-surface">{value}</dd>
                        </div>
                      ))}
                    </dl>
                    <p className="text-[12px] text-on-surface-variant/70">{updatedText(p.updated_at)}</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </main>
  );
}
