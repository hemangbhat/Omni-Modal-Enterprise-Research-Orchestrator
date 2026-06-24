"use client";

import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import { getAdminStats, type AdminStats } from "@/lib/saas-api";

function StatCard({ icon, label, value }: { icon: string; label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-lg">
      <div className="mb-md flex h-9 w-9 items-center justify-center rounded-lg bg-surface-bright text-primary-fixed-dim">
        <MaterialIcon name={icon} size={18} />
      </div>
      <div className="font-headline-md text-headline-md font-bold text-on-surface">{value}</div>
      <div className="font-body-md text-[13px] text-on-surface-variant">{label}</div>
    </div>
  );
}

export default function AdminPage() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    setLoading(true);
    getAdminStats()
      .then((data) => {
        setStats(data);
        setError(null);
      })
      .catch((err: Error) => {
        if (err.message.includes("403")) setForbidden(true);
        else setError(err.message);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="relative flex flex-1 overflow-hidden bg-surface">
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar searchPlaceholder="Search across workspace..." />
        <div className="flex-1 overflow-y-auto px-xl py-lg">
          <div className="mx-auto flex w-full max-w-[1200px] flex-col">
            <div className="mb-lg">
              <h2 className="mb-xs font-headline-lg text-headline-lg text-on-surface">
                Admin Console
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Operational overview of your organization. Requires the admin role.
              </p>
            </div>

            {forbidden ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-amber-400/20 bg-amber-400/5 p-xl text-center">
                <MaterialIcon name="lock" size={40} className="mb-md text-amber-400" />
                <h3 className="mb-sm font-headline-md text-headline-md text-on-surface">
                  Admin access required
                </h3>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  Your account does not have the admin role. Ask an organization admin for access.
                </p>
              </div>
            ) : error ? (
              <div className="rounded-lg border border-error/20 bg-error-container/10 px-md py-sm font-body-md text-[13px] text-error">
                {error}
              </div>
            ) : loading || !stats ? (
              <div className="flex items-center justify-center py-xl">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
              </div>
            ) : (
              <div className="flex flex-col gap-lg">
                {/* Org summary */}
                <div className="grid grid-cols-2 gap-md md:grid-cols-4">
                  <StatCard icon="group" label="Members" value={stats.members} />
                  <StatCard icon="workspaces" label="Workspaces" value={stats.workspaces} />
                  <StatCard icon="receipt_long" label="Audit events" value={stats.audit_events} />
                  <StatCard
                    icon="payments"
                    label="Billing mode"
                    value={stats.billing_mode}
                  />
                </div>

                {/* Org card + adapters */}
                <div className="grid grid-cols-1 gap-lg md:grid-cols-2">
                  <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-lg">
                    <h3 className="mb-md font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                      Organization
                    </h3>
                    <dl className="flex flex-col gap-sm font-body-md text-[13px]">
                      <Row label="Name" value={stats.organization.name} />
                      <Row label="Plan" value={stats.organization.plan_id} />
                      <Row label="Tenant" value={stats.organization.tenant_id} />
                      <Row label="Owner" value={stats.organization.owner_user_id} />
                    </dl>
                  </div>

                  <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-lg">
                    <h3 className="mb-md font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                      Active adapters
                    </h3>
                    <dl className="flex flex-col gap-sm font-body-md text-[13px]">
                      <Row label="Storage" value={stats.adapters.storage} />
                      <Row label="Email" value={stats.adapters.email} />
                      <Row label="Analytics" value={stats.adapters.analytics} />
                    </dl>
                    <p className="mt-md font-mono-sm text-[11px] text-on-surface-variant/70">
                      Local adapters are active by default. Set the matching credentials to
                      switch to S3 / Resend / PostHog.
                    </p>
                  </div>
                </div>

                {/* Event counts */}
                <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-lg">
                  <h3 className="mb-md font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                    Product events
                  </h3>
                  {Object.keys(stats.event_counts).length === 0 ? (
                    <p className="font-body-md text-[13px] text-on-surface-variant">
                      No events captured yet. Run a query or upload a document.
                    </p>
                  ) : (
                    <div className="flex flex-wrap gap-md">
                      {Object.entries(stats.event_counts).map(([event, count]) => (
                        <div
                          key={event}
                          className="flex items-center gap-sm rounded-lg border border-outline-variant/20 bg-surface-container px-md py-sm"
                        >
                          <span className="font-body-md text-[13px] capitalize text-on-surface-variant">
                            {event.replace(/_/g, " ")}
                          </span>
                          <span className="font-mono-sm text-[13px] font-bold text-primary-fixed-dim">
                            {count}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-on-surface-variant">{label}</dt>
      <dd className="font-mono-sm capitalize text-on-surface">{value}</dd>
    </div>
  );
}
