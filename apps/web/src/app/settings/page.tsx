"use client";

import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

const TABS = ["General", "Models", "Security", "Infrastructure"] as const;
type Tab = (typeof TABS)[number];

type ComponentEntry = { name: string; state: string; detail: string };
type HealthData = { phase: number; status: string; components: ComponentEntry[] };

const users = [
  { name: "Sarah Jenkins", email: "sarah.j@omero.inc", role: "Admin", active: "Just now" },
  { name: "Dr. Marcus Chen", email: "m.chen@omero.inc", role: "Lead Researcher", active: "2 hours ago" },
  { name: "API Service Account", email: "svc_pipeline_prod", role: "System", active: "Continuous" }
];

const STATE_DOT: Record<string, string> = {
  ready: "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]",
  deferred: "bg-amber-400",
  contract: "bg-outline"
};
const STATE_TEXT: Record<string, string> = {
  ready: "text-emerald-400",
  deferred: "text-amber-400",
  contract: "text-on-surface-variant"
};

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("Infrastructure");
  const [health, setHealth] = useState<HealthData | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  useEffect(() => {
    const { baseUrl } = getClientApiConfig();
    setHealthLoading(true);
    apiRequest("/health", { method: "GET" }, { baseUrl })
      .then((r) => r.json())
      .then((data: HealthData) => setHealth(data))
      .catch(() => null)
      .finally(() => setHealthLoading(false));
  }, []);

  // Map component names to display info for the health cards
  const compMap = Object.fromEntries(
    (health?.components ?? []).map((c) => [c.name, c])
  );

  const dbComp = compMap["internal_retrieval"];
  const embedComp = compMap["embedding"];
  const adkComp = compMap["adk_orchestration"];
  const whisperComp = compMap["whisper_transcription"];
  const nerComp = compMap["entity_extraction"];
  const sentryDetail = health?.status === "ok" ? "Backend connected" : "Not connected";

  return (
    <main className="relative flex flex-1 flex-col overflow-hidden bg-background">
      <TopBar searchPlaceholder="Search workspace..." />
      <div className="flex-1 overflow-y-auto px-xl py-xl">
        <div className="mb-lg">
          <h1 className="font-headline-lg text-headline-lg text-on-surface">Workspace Settings</h1>
          <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
            Manage system configurations, access controls, and infrastructure health.
          </p>
        </div>

        {/* Tabs */}
        <div className="mb-lg flex border-b border-outline-variant">
          {TABS.map((item) => {
            const active = item === tab;
            return (
              <button
                key={item}
                onClick={() => setTab(item)}
                className={
                  active
                    ? "border-b-2 border-primary px-md py-3 font-label-md text-label-md font-bold text-primary"
                    : "px-md py-3 font-label-md text-label-md text-on-surface-variant transition-colors hover:text-on-surface"
                }
              >
                {item}
              </button>
            );
          })}
        </div>

        {tab === "Infrastructure" ? (
          <div className="space-y-xl">
            {/* System health — from live /health endpoint */}
            <section>
              <h2 className="mb-md font-headline-md text-headline-md text-on-surface">System Health</h2>
              {healthLoading ? (
                <div className="flex items-center gap-md py-lg text-on-surface-variant">
                  <div className="h-6 w-6 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
                  <span className="font-body-md text-body-md">Loading health data…</span>
                </div>
              ) : health ? (
                <div className="grid grid-cols-1 gap-gutter md:grid-cols-3">
                  {/* Retrieval */}
                  <LiveHealthCard
                    title="Retrieval Engine"
                    icon="storage"
                    state={dbComp?.state ?? "deferred"}
                    detail={dbComp?.detail ?? "Not configured"}
                  />
                  {/* Embedding */}
                  <LiveHealthCard
                    title="Embedding Backend"
                    icon="scatter_plot"
                    state={embedComp?.state ?? "deferred"}
                    detail={embedComp?.detail ?? "Not configured"}
                  />
                  {/* ADK Orchestration */}
                  <LiveHealthCard
                    title="ADK Orchestration"
                    icon="account_tree"
                    state={adkComp?.state ?? "deferred"}
                    detail={adkComp?.detail?.split(":")[0] ?? "Not configured"}
                  />
                  {/* Whisper */}
                  <LiveHealthCard
                    title="Whisper Transcription"
                    icon="mic"
                    state={whisperComp?.state ?? "deferred"}
                    detail={whisperComp?.detail ?? "Not configured"}
                  />
                  {/* NER */}
                  <LiveHealthCard
                    title="Entity Extraction"
                    icon="label"
                    state={nerComp?.state ?? "deferred"}
                    detail={nerComp?.detail ?? "Not configured"}
                  />
                  {/* Sentry (inferred from health status) */}
                  <LiveHealthCard
                    title="Sentry Observability"
                    icon="bug_report"
                    state={health.status === "ok" ? "ready" : "deferred"}
                    detail={sentryDetail}
                  />
                </div>
              ) : (
                <div className="rounded-lg border border-error/20 bg-error-container/10 p-md text-error">
                  Failed to load health data. Ensure the backend is running.
                </div>
              )}
            </section>

            {/* Model config */}
            <section>
              <h2 className="mb-md font-headline-md text-headline-md text-on-surface">Model Configuration</h2>
              <div className="rounded-lg border border-outline-variant/40 bg-surface-container-low p-lg">
                <div className="max-w-md">
                  <label className="mb-2 block font-label-md text-label-md text-on-surface">
                    Primary Inference Engine
                  </label>
                  <p className="mb-4 font-body-md text-[14px] text-on-surface-variant">
                    Select the default model for agentic workflows and document summarization.
                  </p>
                  <div className="relative">
                    <select className="w-full appearance-none rounded-md border border-outline-variant bg-surface-container py-3 pl-4 pr-10 font-body-md text-body-md text-on-surface transition-all focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary">
                      <option>GPT-4o (Production)</option>
                      <option>Claude 3.5 Sonnet (Evaluation)</option>
                      <option>Llama 3 70B (Local Fallback)</option>
                    </select>
                    <MaterialIcon
                      name="expand_more"
                      className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
                    />
                  </div>
                  <div className="mt-6 flex items-center gap-2">
                    <MaterialIcon name="info" size={16} className="text-tertiary-container" />
                    <span className="font-mono-sm text-[11px] text-on-surface-variant">
                      Switching engines may momentarily pause active background tasks.
                    </span>
                  </div>
                </div>
              </div>
            </section>

            {/* Access control */}
            <section>
              <div className="mb-md flex items-end justify-between">
                <div>
                  <h2 className="font-headline-md text-headline-md text-on-surface">Access Control</h2>
                  <p className="mt-1 font-body-md text-[14px] text-on-surface-variant">
                    Manage user roles and system access tokens.
                  </p>
                </div>
                <button className="flex items-center gap-2 rounded border border-outline-variant/40 px-4 py-2 font-label-md text-label-md text-primary transition-colors hover:bg-surface-variant">
                  <MaterialIcon name="person_add" size={18} /> Invite User
                </button>
              </div>
              <div className="w-full overflow-x-auto rounded-lg border border-outline-variant/40 bg-surface-container-low">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-outline-variant/40">
                      <th className="px-4 py-3 font-label-md text-label-md font-medium text-on-surface-variant">User / Email</th>
                      <th className="px-4 py-3 font-label-md text-label-md font-medium text-on-surface-variant">Role</th>
                      <th className="px-4 py-3 font-label-md text-label-md font-medium text-on-surface-variant">Last Active</th>
                      <th className="px-4 py-3 font-label-md text-label-md font-medium text-on-surface-variant">Status</th>
                      <th className="px-4 py-3 text-right font-label-md text-label-md font-medium text-on-surface-variant">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="font-body-md text-[14px] text-on-surface">
                    {users.map((user, index) => (
                      <tr
                        key={user.email}
                        className={`group transition-colors hover:bg-surface-variant/50 ${
                          index < users.length - 1 ? "border-b border-outline-variant/40" : ""
                        }`}
                      >
                        <td className="px-4 py-3">
                          <div className="font-medium transition-colors group-hover:text-primary">{user.name}</div>
                          <div className="mt-0.5 font-mono-sm text-[11px] text-on-surface-variant">{user.email}</div>
                        </td>
                        <td className="px-4 py-3">
                          <span className="rounded border border-outline-variant bg-surface-container px-2 py-1 font-mono-sm text-[11px]">
                            {user.role}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-on-surface-variant">{user.active}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            <div className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                            <span>Active</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button className="text-on-surface-variant transition-colors hover:text-primary">
                            <MaterialIcon name="more_vert" size={20} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        ) : (
          <div className="rounded-lg border border-outline-variant/40 bg-surface-container-low p-xl text-center">
            <MaterialIcon name="settings" size={32} className="text-on-surface-variant" />
            <p className="mt-md font-body-md text-body-md text-on-surface-variant">
              {tab} settings are part of this workspace configuration.
            </p>
          </div>
        )}
      </div>
    </main>
  );
}

function LiveHealthCard({
  title,
  icon,
  state,
  detail
}: {
  title: string;
  icon: string;
  state: string;
  detail: string;
}) {
  const dotClass = STATE_DOT[state] ?? "bg-outline";
  const textClass = STATE_TEXT[state] ?? "text-on-surface-variant";
  const borderClass = state === "ready" ? "border-t-primary" : state === "deferred" ? "border-t-amber-500" : "border-t-outline";
  return (
    <div
      className={`group relative flex flex-col gap-sm overflow-hidden rounded border border-outline-variant/40 border-t-2 ${borderClass} bg-surface-container-low p-4`}
    >
      <div className="absolute -right-10 -top-10 h-32 w-32 rounded-full bg-primary/5 blur-2xl transition-all group-hover:bg-primary/10" />
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2 text-on-surface">
          <MaterialIcon name={icon} size={20} className="text-primary" />
          <span className="font-label-md text-label-md font-bold">{title}</span>
        </div>
        <div className="flex items-center gap-1.5 rounded border border-outline-variant bg-surface-variant/50 px-2 py-0.5">
          <div className={`h-1.5 w-1.5 rounded-full ${dotClass}`} />
          <span className={`font-mono-sm text-[11px] capitalize ${textClass}`}>{state}</span>
        </div>
      </div>
      <p className="mt-2 line-clamp-2 font-body-md text-[13px] text-on-surface-variant">{detail}</p>
    </div>
  );
}
