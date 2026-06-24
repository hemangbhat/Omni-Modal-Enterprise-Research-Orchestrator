"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import { getUsage, type UsageReport } from "@/lib/saas-api";

const METRIC_LABELS: Record<string, { label: string; icon: string; unit: string }> = {
  queries: { label: "Research queries", icon: "search", unit: "this month" },
  uploads: { label: "Document uploads", icon: "cloud_upload", unit: "this month" },
  workspaces: { label: "Workspaces", icon: "workspaces", unit: "total" },
  members: { label: "Team members", icon: "group", unit: "total" },
  storage_mb: { label: "Storage", icon: "database", unit: "MB" }
};

function barColor(percent: number, unlimited: boolean): string {
  if (unlimited) return "bg-primary-fixed-dim";
  if (percent >= 90) return "bg-error";
  if (percent >= 75) return "bg-amber-400";
  return "bg-primary-fixed-dim";
}

export default function UsagePage() {
  const [report, setReport] = useState<UsageReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getUsage()
      .then((data) => {
        setReport(data);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="relative flex flex-1 overflow-hidden bg-surface">
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar searchPlaceholder="Search across workspace..." />
        <div className="flex-1 overflow-y-auto px-xl py-lg">
          <div className="mx-auto flex w-full max-w-[1100px] flex-col">
            <div className="mb-lg flex flex-col justify-between gap-md sm:flex-row sm:items-end">
              <div>
                <h2 className="mb-xs font-headline-lg text-headline-lg text-on-surface">
                  Usage &amp; Limits
                </h2>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  {report
                    ? `You're on the ${report.plan.name} plan. Limits reset monthly.`
                    : "Monitor your monthly consumption against plan limits."}
                </p>
              </div>
              <Link
                href="/billing"
                className="flex items-center gap-sm rounded border border-primary-fixed-dim bg-primary px-md py-[8px] font-label-md text-label-md font-medium text-on-primary transition-all hover:bg-primary-fixed"
              >
                <MaterialIcon name="upgrade" size={18} />
                Manage plan
              </Link>
            </div>

            {error ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-error/20 bg-error-container/10 p-xl text-center">
                <MaterialIcon name="error_outline" size={40} className="mb-md text-error" />
                <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">
                  Failed to load usage
                </h3>
                <p className="font-body-md text-body-md text-on-surface-variant">{error}</p>
              </div>
            ) : loading || !report ? (
              <div className="flex items-center justify-center py-xl">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-md md:grid-cols-2">
                {Object.entries(report.metrics).map(([key, metric]) => {
                  const meta = METRIC_LABELS[key] ?? {
                    label: key,
                    icon: "analytics",
                    unit: ""
                  };
                  return (
                    <div
                      key={key}
                      className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-lg"
                    >
                      <div className="mb-md flex items-center justify-between">
                        <div className="flex items-center gap-sm">
                          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-bright text-primary-fixed-dim">
                            <MaterialIcon name={meta.icon} size={18} />
                          </div>
                          <span className="font-label-md text-label-md text-on-surface">
                            {meta.label}
                          </span>
                        </div>
                        <span className="font-mono-sm text-[12px] text-on-surface-variant">
                          {meta.unit}
                        </span>
                      </div>
                      <div className="mb-sm flex items-end justify-between">
                        <span className="font-headline-md text-headline-md font-bold text-on-surface">
                          {metric.used}
                        </span>
                        <span className="font-mono-sm text-[12px] text-on-surface-variant">
                          {metric.unlimited ? "Unlimited" : `of ${metric.limit}`}
                        </span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-surface-bright">
                        <div
                          className={`h-full rounded-full transition-all ${barColor(
                            metric.percent,
                            metric.unlimited
                          )}`}
                          style={{
                            width: metric.unlimited
                              ? "8%"
                              : `${Math.min(100, metric.percent)}%`
                          }}
                        />
                      </div>
                      {!metric.unlimited && metric.percent >= 75 ? (
                        <p className="mt-sm font-mono-sm text-[11px] text-amber-400">
                          {metric.percent >= 90
                            ? "Limit nearly reached — consider upgrading."
                            : "Approaching your plan limit."}
                        </p>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
