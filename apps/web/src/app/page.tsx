"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

type ComponentEntry = { name: string; state: string; detail: string };

type HealthData = {
  phase: number;
  status: string;
  components: ComponentEntry[];
};

const STATE_COLOR: Record<string, string> = {
  ready: "text-emerald-400",
  deferred: "text-amber-400",
  contract: "text-on-surface-variant"
};
const STATE_DOT: Record<string, string> = {
  ready: "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]",
  deferred: "bg-amber-400",
  contract: "bg-outline"
};

export default function HomePage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [docCount, setDocCount] = useState<number | null>(null);

  useEffect(() => {
    const { baseUrl, token } = getClientApiConfig();
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;

    // Fetch /health (public)
    apiRequest("/health", { method: "GET" }, { baseUrl })
      .then((r) => r.json())
      .then((data: HealthData) => setHealth(data))
      .catch(() => null);

    // Fetch /documents for doc count
    apiRequest("/documents", { method: "GET", headers }, { baseUrl })
      .then((r) => r.json())
      .then((data: { total: number }) => setDocCount(data.total ?? null))
      .catch(() => null);
  }, []);

  const systemOk = health?.status === "ok";
  const readyCount = health?.components.filter((c) => c.state === "ready").length ?? 0;
  const totalCount = health?.components.length ?? 0;

  return (
    <main className="relative flex flex-1 flex-col bg-surface">
      <div className="mx-auto flex w-full max-w-max_width flex-col gap-[48px] px-lg py-xl md:px-xl">

        {/* Hero */}
        <section className="premium-card relative flex flex-col items-start justify-between gap-lg overflow-hidden rounded-xl p-xl md:flex-row md:items-center">
          <div className="hero-glow" />
          <div className="relative z-10">
            <p className="mb-sm font-mono-sm text-mono-sm uppercase tracking-widest text-primary-fixed-dim">
              {health ? (systemOk ? "System Status: Optimal" : "System Status: Degraded") : "Connecting to backend…"}
            </p>
            <h2 className="mb-sm font-display-lg text-display-lg text-on-surface">
              Welcome, Researcher
            </h2>
            <p className="max-w-2xl font-body-lg text-body-lg text-on-surface-variant">
              {health
                ? `${readyCount} of ${totalCount} components ready. ${docCount !== null ? `${docCount} document${docCount !== 1 ? "s" : ""} in your knowledge base.` : ""}`
                : "Upload documents, run research queries, and monitor your pipeline."}
            </p>
          </div>
          <div className="relative z-10 shrink-0">
            <Link
              href="/research"
              className="flex items-center gap-sm rounded-lg bg-primary-container px-lg py-md font-label-md text-label-md text-on-primary-container shadow-[0_0_20px_rgba(0,229,255,0.2)] transition-colors hover:bg-primary"
            >
              <MaterialIcon name="bolt" size={18} />
              Start Research
            </Link>
          </div>
        </section>

        {/* Live metrics from /health + /documents */}
        <section className="grid grid-cols-1 gap-lg md:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            label="Documents Indexed"
            value={docCount !== null ? String(docCount) : "—"}
            icon="description"
          />
          <MetricCard
            label="Components Ready"
            value={health ? `${readyCount}/${totalCount}` : "—"}
            icon="check_circle"
            note={health && systemOk ? "All green" : health ? "Check settings" : undefined}
          />
          <MetricCard
            label="Embedding Backend"
            value={
              health?.components.find((c) => c.name === "embedding")?.detail
                ?.replace(/^Embedding backend: /, "")
                .split(" ")[0] ?? "—"
            }
            icon="scatter_plot"
          />
          <MetricCard
            label="Pipeline Phase"
            value={health ? `Phase ${health.phase}` : "—"}
            icon="dns"
            dot={systemOk}
          />
        </section>

        {/* Component health + ingestion zone */}
        <div className="grid grid-cols-1 gap-xl lg:grid-cols-3">
          {/* Component list */}
          <div className="flex flex-col gap-md lg:col-span-2">
            <div className="flex items-center justify-between">
              <h3 className="font-headline-md text-headline-md text-on-surface">System Components</h3>
              <Link
                href="/settings"
                className="flex items-center gap-xs font-label-md text-label-md text-primary-fixed-dim transition-colors hover:text-primary"
              >
                View Settings <MaterialIcon name="arrow_forward" size={16} />
              </Link>
            </div>
            <div className="premium-card overflow-hidden rounded-xl">
              {health ? (
                health.components.map((comp, idx) => (
                  <div
                    key={comp.name}
                    className={`flex items-start gap-md p-lg transition-colors hover:bg-surface-container-high/30 ${
                      idx < health.components.length - 1 ? "border-b border-outline-variant/20" : ""
                    }`}
                  >
                    <div className={`mt-1 h-2 w-2 flex-shrink-0 rounded-full ${STATE_DOT[comp.state] ?? "bg-outline"}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-sm">
                        <span className="font-label-md text-label-md text-on-surface capitalize">
                          {comp.name.replace(/_/g, " ")}
                        </span>
                        <span className={`font-mono-sm text-[10px] uppercase tracking-wider ${STATE_COLOR[comp.state] ?? "text-on-surface-variant"}`}>
                          {comp.state}
                        </span>
                      </div>
                      <p className="mt-xs truncate font-mono-sm text-[11px] text-on-surface-variant/70">
                        {comp.detail}
                      </p>
                    </div>
                  </div>
                ))
              ) : (
                <div className="p-xl text-center">
                  <div className="mx-auto mb-md h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
                  <p className="font-body-md text-body-md text-on-surface-variant">Loading system status…</p>
                </div>
              )}
            </div>
          </div>

          {/* Quick upload */}
          <div className="flex flex-col gap-md">
            <h3 className="font-headline-md text-headline-md text-on-surface">Quick Actions</h3>
            <div className="premium-card flex h-full flex-col rounded-xl p-lg">
              <p className="mb-lg font-body-md text-body-md text-on-surface-variant">
                Upload documents for ingestion, transcription, and research.
              </p>
              <Link
                href="/upload"
                className="glass-dropzone flex min-h-[180px] flex-1 cursor-pointer flex-col items-center justify-center rounded-lg p-xl text-center"
              >
                <div className="mb-md flex h-12 w-12 items-center justify-center rounded-full bg-surface-container-high text-primary-fixed-dim shadow-inner">
                  <MaterialIcon name="cloud_upload" size={24} />
                </div>
                <h4 className="mb-xs font-label-md text-label-md text-on-surface">Upload Documents</h4>
                <p className="font-mono-sm text-mono-sm text-on-surface-variant">PDF and audio files</p>
              </Link>
              <div className="mt-md flex flex-col gap-sm">
                <Link
                  href="/research"
                  className="flex items-center justify-center gap-sm rounded-lg border border-outline-variant/30 py-sm font-label-md text-label-md text-on-surface transition-colors hover:bg-surface-container-high"
                >
                  <MaterialIcon name="science" size={16} />
                  New Research Query
                </Link>
                <Link
                  href="/documents"
                  className="flex items-center justify-center gap-sm rounded-lg border border-outline-variant/30 py-sm font-label-md text-label-md text-on-surface transition-colors hover:bg-surface-container-high"
                >
                  <MaterialIcon name="description" size={16} />
                  View Knowledge Base
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>

      <footer className="mt-auto flex w-full items-center justify-between border-t border-outline-variant/10 bg-surface-container-lowest px-xl py-md">
        <span className="font-mono-sm text-mono-sm text-secondary-fixed-dim">
          © 2024 OMERO Precision AI. Enterprise Research Platform.
        </span>
        <div className="hidden gap-lg md:flex">
          <span className="font-mono-sm text-mono-sm text-on-surface-variant/60">Privacy Policy</span>
          <span className="font-mono-sm text-mono-sm text-on-surface-variant/60">Terms of Service</span>
          <span className="font-mono-sm text-mono-sm text-on-surface-variant/60">Security</span>
        </div>
      </footer>
    </main>
  );
}

function MetricCard({
  label,
  value,
  icon,
  note,
  dot
}: {
  label: string;
  value: string;
  icon: string;
  note?: string;
  dot?: boolean;
}) {
  return (
    <div className="premium-card flex flex-col rounded-lg p-lg">
      <div className="mb-md flex items-center justify-between">
        <span className="font-label-md text-label-md text-on-surface-variant">{label}</span>
        <MaterialIcon name={icon} size={20} className="text-outline" />
      </div>
      <div className="flex items-end gap-sm">
        <span className="font-headline-lg text-headline-lg text-on-surface">{value}</span>
        {note ? (
          <span className="mb-1 font-mono-sm text-mono-sm text-primary-fixed-dim">{note}</span>
        ) : null}
        {dot ? (
          <div className="mb-2 ml-1 h-2 w-2 rounded-full bg-primary-fixed-dim shadow-[0_0_8px_rgba(0,218,243,0.8)]" />
        ) : null}
      </div>
    </div>
  );
}
