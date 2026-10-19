"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { FirstRunChecklist } from "@/components/first-run-checklist";
import { TopBar } from "@/components/top-bar";
import { useClaims } from "@/lib/use-claims";
import { describeActivity } from "@/lib/projects-api";
import { getDashboard, type Dashboard } from "@/lib/workspace-api";

const STAT_TILES: { key: "ready" | "processing" | "failed"; label: string; href: string }[] = [
  { key: "ready", label: "Ready to search", href: "/documents?status=ready" },
  { key: "processing", label: "Processing", href: "/documents?status=processing" },
  { key: "failed", label: "Failed", href: "/documents?status=failed" }
];

function ago(epochSeconds: number): string {
  const seconds = Date.now() / 1000 - epochSeconds;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

export default function HomePage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const claims = useClaims();

  useEffect(() => {
    getDashboard()
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  const usage = data?.usage;

  return (
    <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-surface">
      <TopBar searchPlaceholder="Search documents, projects, research..." />
      <div className="flex-1 overflow-y-auto px-lg py-xl md:px-xl">
        <div className="mx-auto flex max-w-6xl flex-col gap-xl">
          <div className="flex flex-col justify-between gap-md md:flex-row md:items-end">
            <div>
              <h2 className="text-[30px] font-semibold tracking-tight text-on-surface">Workspace</h2>
              <p className="mt-xs text-[16px] text-on-surface-variant">
                {data
                  ? `${data.documents.total} document${data.documents.total === 1 ? "" : "s"} and ${data.projects} active project${data.projects === 1 ? "" : "s"}.`
                  : "Loading your workspace…"}
              </p>
            </div>
            <div className="flex gap-sm">
              <Link href="/upload" className="flex items-center gap-xs rounded-lg border border-outline-variant/40 px-md py-sm text-[14px] text-on-surface hover:bg-surface-container">
                <MaterialIcon name="upload_file" size={18} /> Upload
              </Link>
              <Link href="/research" className="flex items-center gap-xs rounded-lg bg-primary-fixed-dim px-md py-sm text-[14px] font-semibold text-on-primary-fixed hover:opacity-90">
                <MaterialIcon name="science" size={18} /> Ask a question
              </Link>
            </div>
          </div>

          {error ? <p role="alert" className="text-error">{error}</p> : null}

          {data?.progress && claims ? <FirstRunChecklist progress={data.progress} userId={claims.user_id} /> : null}

          {data ? (
            <div className="grid grid-cols-1 gap-md sm:grid-cols-2 lg:grid-cols-4">
              {STAT_TILES.map((tile) => (
                <Link
                  key={tile.key}
                  href={tile.href}
                  className="rounded-2xl border border-outline-variant/30 bg-surface-container-low p-md hover:border-primary-fixed-dim/50"
                >
                  <span className="block text-[13px] text-on-surface-variant">{tile.label}</span>
                  <span className={`text-[28px] font-semibold ${tile.key === "failed" && data.documents.failed > 0 ? "text-error" : "text-on-surface"}`}>
                    {data.documents[tile.key]}
                  </span>
                </Link>
              ))}
              <Link href="/projects" className="rounded-2xl border border-outline-variant/30 bg-surface-container-low p-md hover:border-primary-fixed-dim/50">
                <span className="block text-[13px] text-on-surface-variant">Active projects</span>
                <span className="text-[28px] font-semibold text-on-surface">{data.projects}</span>
              </Link>
            </div>
          ) : null}

          {data ? (
            <div className="grid grid-cols-1 gap-xl lg:grid-cols-3">
              <section aria-labelledby="recent-docs" className="flex flex-col gap-sm lg:col-span-2">
                <div className="flex items-baseline justify-between">
                  <h3 id="recent-docs" className="text-[17px] font-semibold text-on-surface">Recent uploads</h3>
                  <Link href="/documents" className="text-[14px] text-primary-fixed-dim hover:underline">All documents</Link>
                </div>
                {data.recent_documents.length === 0 ? (
                  <p className="text-[14px] text-on-surface-variant">Nothing uploaded yet.</p>
                ) : (
                  <ul className="divide-y divide-outline-variant/20 rounded-xl border border-outline-variant/30">
                    {data.recent_documents.map((d) => (
                      <li key={d.id} className="flex items-center justify-between gap-md px-md py-sm">
                        <span className="min-w-0 truncate text-[15px] text-on-surface">{d.title}</span>
                        <span className={`shrink-0 text-[13px] ${d.status === "failed" ? "text-error" : "text-on-surface-variant"}`}>
                          {d.status === "ready" ? `Ready, ${ago(d.created_at)}` : d.status === "failed" ? "Failed" : "Processing"}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                <h3 className="mt-lg text-[17px] font-semibold text-on-surface">Your recent research</h3>
                {data.recent_sessions.length === 0 ? (
                  <p className="text-[14px] text-on-surface-variant">No questions asked yet.</p>
                ) : (
                  <ul className="flex flex-col gap-xs">
                    {data.recent_sessions.map((s) => (
                      <li key={s.id} className="flex items-baseline justify-between gap-md">
                        <Link href={`/research?session=${s.id}`} className="min-w-0 truncate text-[15px] text-on-surface hover:text-primary-fixed-dim">
                          {s.title}
                        </Link>
                        <span className="shrink-0 text-[13px] text-on-surface-variant">{ago(s.updated_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <aside className="flex flex-col gap-xl">
                {usage ? (
                  <section aria-labelledby="usage-heading" className="flex flex-col gap-sm">
                    <h3 id="usage-heading" className="text-[17px] font-semibold text-on-surface">
                      This month on the {usage.plan.name} plan
                    </h3>
                    {(["queries", "uploads"] as const).map((metric) => {
                      const m = usage.metrics[metric];
                      if (!m) return null;
                      const pct = m.unlimited ? 0 : Math.min(100, m.percent);
                      return (
                        <div key={metric}>
                          <div className="flex justify-between text-[14px]">
                            <span className="capitalize text-on-surface-variant">{metric}</span>
                            <span className="text-on-surface">{m.unlimited ? `${m.used} (unlimited)` : `${m.used} of ${m.limit}`}</span>
                          </div>
                          {!m.unlimited ? (
                            <div className="mt-xs h-[6px] overflow-hidden rounded-full bg-surface-container-highest" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${metric} used`}>
                              <div className={`h-full rounded-full ${pct >= 90 ? "bg-error" : "bg-primary-fixed-dim"}`} style={{ width: `${pct}%` }} />
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                    <Link href="/usage" className="text-[14px] text-primary-fixed-dim hover:underline">Usage details</Link>
                  </section>
                ) : null}

                <section aria-labelledby="activity-heading" className="flex flex-col gap-sm">
                  <h3 id="activity-heading" className="text-[17px] font-semibold text-on-surface">Project activity</h3>
                  {data.recent_activity.length === 0 ? (
                    <p className="text-[14px] text-on-surface-variant">No project activity yet.</p>
                  ) : (
                    <ol className="flex flex-col gap-sm">
                      {data.recent_activity.map((a) => (
                        <li key={a.id} className="text-[14px] leading-snug">
                          <Link href={`/projects/${a.project_id}`} className="text-on-surface hover:text-primary-fixed-dim">
                            {describeActivity(a)}
                          </Link>
                          <span className="block text-[12px] text-on-surface-variant">
                            {a.project_name ?? "Project"}, {ago(a.created_at)}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
              </aside>
            </div>
          ) : null}
        </div>
      </div>
    </main>
  );
}
