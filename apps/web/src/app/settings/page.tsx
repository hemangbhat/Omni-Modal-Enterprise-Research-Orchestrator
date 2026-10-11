"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { MfaSettings } from "@/components/mfa-settings";
import { TopBar } from "@/components/top-bar";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

const TABS = ["Security", "Infrastructure"] as const;
type Tab = (typeof TABS)[number];

type ComponentEntry = { name: string; state: string; detail: string };
type HealthData = { phase: number; status: string; components: ComponentEntry[] };

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

// Backend /health components shown on the Infrastructure tab, in display order.
const HEALTH_CARDS: { component: string; title: string; icon: string }[] = [
  { component: "internal_retrieval", title: "Retrieval Engine", icon: "storage" },
  { component: "embedding", title: "Embedding Backend", icon: "scatter_plot" },
  { component: "adk_orchestration", title: "Orchestration", icon: "account_tree" },
  { component: "whisper_transcription", title: "Whisper Transcription", icon: "mic" },
  { component: "entity_extraction", title: "Entity Extraction", icon: "label" }
];

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("Security");
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

  const components = Object.fromEntries((health?.components ?? []).map((c) => [c.name, c]));

  return (
    <main className="relative flex flex-1 flex-col overflow-hidden bg-background">
      <TopBar searchPlaceholder="Search workspace..." />
      <div className="flex-1 overflow-y-auto px-xl py-xl">
        <div className="mb-lg">
          <h1 className="font-headline-lg text-headline-lg text-on-surface">Settings</h1>
          <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
            Secure your account and check the health of the backend services.
          </p>
        </div>

        {/* Tabs */}
        <div role="tablist" aria-label="Settings sections" className="mb-lg flex border-b border-outline-variant">
          {TABS.map((item) => {
            const active = item === tab;
            return (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={active}
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

        {tab === "Security" ? (
          <div className="space-y-lg">
            <MfaSettings />
            <p className="font-body-md text-[14px] text-on-surface-variant">
              Members, roles and invitations are managed on the{" "}
              <Link href="/team" className="text-primary-fixed-dim underline hover:text-primary-fixed">
                Team page
              </Link>
              .
            </p>
          </div>
        ) : (
          <section>
            {/* System health — from the live /health endpoint */}
            <h2 className="mb-md font-headline-md text-headline-md text-on-surface">System Health</h2>
            {healthLoading ? (
              <div className="flex items-center gap-md py-lg text-on-surface-variant">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
                <span className="font-body-md text-body-md">Loading health data…</span>
              </div>
            ) : health ? (
              <div className="grid grid-cols-1 gap-gutter md:grid-cols-3">
                {HEALTH_CARDS.map((card) => (
                  <LiveHealthCard
                    key={card.component}
                    title={card.title}
                    icon={card.icon}
                    state={components[card.component]?.state ?? "deferred"}
                    detail={components[card.component]?.detail ?? "Not reported by the backend"}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-error/20 bg-error-container/10 p-md text-error">
                Failed to load health data. Ensure the backend is running.
              </div>
            )}
          </section>
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
