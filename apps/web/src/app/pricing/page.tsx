"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";
import type { Plan } from "@/lib/saas-api";

// Only limits the API actually enforces are listed.
const ROWS: { label: string; value: (p: Plan) => number; unit: string }[] = [
  { label: "Research questions", value: (p) => p.monthly_query_limit, unit: "a month" },
  { label: "Document uploads", value: (p) => p.monthly_upload_limit, unit: "a month" },
  { label: "Web searches when documents lack the answer", value: (p) => p.monthly_external_research_limit ?? 0, unit: "a month" },
  { label: "Workspaces", value: (p) => p.max_workspaces, unit: "" },
  { label: "Team members", value: (p) => p.max_members, unit: "" }
];

function amount(value: number, unit: string): string {
  if (value < 0) return "Unlimited";
  if (value === 0) return "Not included";
  return `${value.toLocaleString()}${unit ? ` ${unit}` : ""}`;
}

function price(plan: Plan): { figure: string; note: string } {
  if (plan.id === "enterprise") return { figure: "Custom", note: "Price agreed directly" };
  if (plan.price_usd_month === 0) return { figure: "$0", note: "No card needed" };
  return { figure: `$${plan.price_usd_month}`, note: "per month, billed through Stripe" };
}

export default function PricingPage() {
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const { baseUrl } = getClientApiConfig();
    apiRequest("/plans", { method: "GET" }, { baseUrl })
      .then((r) => {
        if (!r.ok) throw new Error(`Plans could not be loaded (HTTP ${r.status}).`);
        return r.json() as Promise<{ plans: Plan[] }>;
      })
      .then((data) => setPlans(data.plans ?? []))
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <div className="min-h-screen bg-surface text-on-surface">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-lg py-lg">
        <Link href="/" className="flex items-center gap-sm">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-fixed-dim text-on-primary-fixed">
            <MaterialIcon name="science" size={18} fill />
          </span>
          <span className="text-[18px] font-black tracking-tight">OMERO</span>
        </Link>
        <nav className="flex items-center gap-md text-[14px]">
          <Link href="/sign-in" className="text-on-surface-variant hover:text-on-surface">Sign in</Link>
          <Link href="/sign-up" className="rounded-lg bg-primary-fixed-dim px-md py-xs font-semibold text-on-primary-fixed hover:opacity-90">
            Create an account
          </Link>
        </nav>
      </header>

      <main className="mx-auto flex max-w-6xl flex-col gap-xl px-lg pb-[96px] pt-xl">
        <div className="max-w-2xl">
          <h1 className="text-[40px] font-semibold leading-tight tracking-tight">Pricing</h1>
          <p className="mt-md text-[17px] leading-relaxed text-on-surface-variant">
            Every plan answers questions from your own documents with cited sources, keeps saved research and
            projects, and runs the same processing for PDFs and recordings. Plans differ only in how much you can
            use each month.
          </p>
        </div>

        {error ? <p role="alert" className="text-error">{error}</p> : null}
        {plans === null && !error ? <p className="text-on-surface-variant">Loading plans…</p> : null}

        {plans && plans.length > 0 ? (
          <div className="grid grid-cols-1 gap-md md:grid-cols-3">
            {plans.map((plan) => {
              const p = price(plan);
              const featured = plan.id === "pro";
              return (
                <section
                  key={plan.id}
                  aria-labelledby={`plan-${plan.id}`}
                  className={`flex flex-col gap-lg rounded-2xl border p-lg ${
                    featured ? "border-primary-fixed-dim/60 bg-surface-container" : "border-outline-variant/30 bg-surface-container-low"
                  }`}
                >
                  <div>
                    <h2 id={`plan-${plan.id}`} className="text-[19px] font-semibold">{plan.name}</h2>
                    <p className="mt-sm text-[36px] font-semibold leading-none">{p.figure}</p>
                    <p className="mt-xs text-[14px] text-on-surface-variant">{p.note}</p>
                  </div>
                  <dl className="flex flex-col gap-sm border-t border-outline-variant/20 pt-md">
                    {ROWS.map((row) => (
                      <div key={row.label} className="flex items-baseline justify-between gap-md text-[15px]">
                        <dt className="text-on-surface-variant">{row.label}</dt>
                        <dd className="text-right font-medium">{amount(row.value(plan), row.unit)}</dd>
                      </div>
                    ))}
                  </dl>
                  {plan.id === "enterprise" ? (
                    <p className="mt-auto rounded-lg border border-dashed border-outline-variant/50 px-md py-sm text-center text-[14px] text-on-surface-variant">
                      Not available to choose online yet
                    </p>
                  ) : (
                  <Link
                    href="/sign-up"
                    className={`mt-auto rounded-lg px-md py-sm text-center text-[14px] font-semibold ${
                      featured
                        ? "bg-primary-fixed-dim text-on-primary-fixed hover:opacity-90"
                        : "border border-outline-variant/50 text-on-surface hover:bg-surface-container-high"
                    }`}
                  >
                    {plan.id === "free" ? "Start free" : "Create an account, then upgrade in Billing"}
                  </Link>
                  )}
                </section>
              );
            })}
          </div>
        ) : null}

        <p className="max-w-2xl text-[14px] leading-relaxed text-on-surface-variant">
          When a monthly limit is reached, new questions or uploads are refused with a message until the next month or
          an upgrade; nothing already saved is removed. Plans can be changed at any time from the Billing page.
        </p>
      </main>
    </div>
  );
}
