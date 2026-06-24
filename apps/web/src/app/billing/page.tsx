"use client";

import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import {
  changePlan,
  confirmCheckout,
  getBilling,
  openBillingPortal,
  startCheckout,
  type Plan
} from "@/lib/saas-api";

const FEATURE_LABELS: Record<string, string> = {
  api_access: "API access",
  external_delegation: "External AI delegation",
  audit_export: "Audit log export",
  priority_ingest: "Priority ingestion",
  sso: "SSO / SAML"
};

function priceLabel(plan: Plan): string {
  if (plan.id === "enterprise") return "Custom";
  return plan.price_usd_month === 0 ? "$0" : `$${plan.price_usd_month}`;
}

export default function BillingPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [currentPlan, setCurrentPlan] = useState<string>("free");
  const [billingMode, setBillingMode] = useState<string>("demo");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [changing, setChanging] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);

  function load() {
    setLoading(true);
    getBilling()
      .then((data) => {
        setPlans(data.plans);
        setCurrentPlan(data.current_plan);
        setBillingMode(data.billing_mode);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  // Handle the Stripe Checkout return redirect (?status=success&session_id=...).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("status");
    const sessionId = params.get("session_id");
    if (status === "cancelled") {
      setNotice("Checkout cancelled — no changes were made.");
      window.history.replaceState({}, "", "/billing");
      return;
    }
    if (status === "success" && sessionId) {
      setNotice("Confirming your subscription…");
      confirmCheckout(sessionId)
        .then((res) => {
          setNotice(
            res.paid
              ? "Subscription active. Your plan has been updated."
              : "Payment not completed. No changes were made."
          );
          load();
        })
        .catch((err: Error) => setError(err.message))
        .finally(() => window.history.replaceState({}, "", "/billing"));
    }
  }, []);

  async function handleChange(planId: string) {
    if (planId === currentPlan) return;
    setChanging(planId);
    setError(null);
    try {
      if (billingMode === "stripe" && planId !== "free" && planId !== "enterprise") {
        // Real Stripe checkout — redirect the browser to the hosted page.
        const { url } = await startCheckout(planId);
        window.location.href = url;
        return; // navigation in progress
      }
      // Demo mode (or free/enterprise): apply locally.
      await changePlan(planId);
      setCurrentPlan(planId);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setChanging(null);
    }
  }

  async function handleManageBilling() {
    setPortalLoading(true);
    setError(null);
    try {
      const { url } = await openBillingPortal();
      window.location.href = url;
    } catch (err) {
      setError((err as Error).message);
      setPortalLoading(false);
    }
  }

  return (
    <main className="relative flex flex-1 overflow-hidden bg-surface">
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar searchPlaceholder="Search across workspace..." />
        <div className="flex-1 overflow-y-auto px-xl py-lg">
          <div className="mx-auto flex w-full max-w-[1100px] flex-col">
            <div className="mb-lg">
              <h2 className="mb-xs font-headline-lg text-headline-lg text-on-surface">
                Billing &amp; Plans
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Choose the plan that fits your team. Limits are enforced in real time.
              </p>
            </div>

            {/* Honest billing-mode banner */}
            <div
              className={`mb-lg flex items-start gap-md rounded-xl border p-md ${
                billingMode === "stripe"
                  ? "border-primary-fixed-dim/30 bg-primary/5"
                  : "border-amber-400/30 bg-amber-400/5"
              }`}
            >
              <MaterialIcon
                name={billingMode === "stripe" ? "verified" : "info"}
                size={20}
                className={billingMode === "stripe" ? "text-primary-fixed-dim" : "text-amber-400"}
              />
              <div>
                <p className="font-label-md text-label-md text-on-surface">
                  {billingMode === "stripe"
                    ? "Live billing (Stripe) is active."
                    : "Demo billing mode"}
                </p>
                <p className="font-body-md text-[13px] text-on-surface-variant">
                  {billingMode === "stripe"
                    ? "Plan changes create real Stripe subscriptions."
                    : "No payment processor is connected, so plan changes apply instantly with no charge. Set STRIPE_SECRET_KEY to enable real billing."}
                </p>
              </div>
              {billingMode === "stripe" ? (
                <button
                  type="button"
                  onClick={handleManageBilling}
                  disabled={portalLoading}
                  className="ml-auto flex items-center gap-sm rounded-lg border border-outline-variant/40 bg-surface-container px-md py-sm font-label-md text-label-md text-on-surface transition-colors hover:border-primary-fixed-dim/50 disabled:opacity-60"
                >
                  {portalLoading ? (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-surface/30 border-t-on-surface" />
                  ) : (
                    <MaterialIcon name="credit_card" size={16} />
                  )}
                  Manage billing
                </button>
              ) : null}
            </div>

            {notice ? (
              <div className="mb-lg flex items-center gap-sm rounded-lg border border-primary-fixed-dim/30 bg-primary/5 px-md py-sm font-body-md text-[13px] text-on-surface">
                <MaterialIcon name="info" size={16} className="text-primary-fixed-dim" />
                {notice}
              </div>
            ) : null}

            {error ? (
              <div className="mb-lg rounded-lg border border-error/20 bg-error-container/10 px-md py-sm font-body-md text-[13px] text-error">
                {error}
              </div>
            ) : null}

            {loading ? (
              <div className="flex items-center justify-center py-xl">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-lg md:grid-cols-3">
                {plans.map((plan) => {
                  const isCurrent = plan.id === currentPlan;
                  return (
                    <div
                      key={plan.id}
                      className={`flex flex-col rounded-2xl border p-lg transition-all ${
                        isCurrent
                          ? "border-primary-fixed-dim bg-surface-container-low shadow-[0_0_24px_rgba(0,218,243,0.12)]"
                          : "border-outline-variant/30 bg-surface-container-low hover:border-outline-variant/60"
                      }`}
                    >
                      <div className="mb-md flex items-center justify-between">
                        <h3 className="font-headline-md text-headline-md font-bold text-on-surface">
                          {plan.name}
                        </h3>
                        {isCurrent ? (
                          <span className="rounded-full border border-primary-fixed-dim/40 bg-primary/10 px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-primary-fixed-dim">
                            Current
                          </span>
                        ) : null}
                      </div>
                      <div className="mb-md flex items-end gap-xs">
                        <span className="font-headline-lg text-headline-lg font-black text-on-surface">
                          {priceLabel(plan)}
                        </span>
                        {plan.id !== "enterprise" ? (
                          <span className="mb-1 font-body-md text-[13px] text-on-surface-variant">
                            /month
                          </span>
                        ) : null}
                      </div>

                      <ul className="mb-lg flex flex-1 flex-col gap-sm">
                        <PlanLine text={limitText(plan.monthly_query_limit, "queries / mo")} />
                        <PlanLine text={limitText(plan.monthly_upload_limit, "uploads / mo")} />
                        <PlanLine text={limitText(plan.max_workspaces, "workspaces")} />
                        <PlanLine text={limitText(plan.max_members, "team members")} />
                        {plan.features.map((f) => (
                          <PlanLine key={f} text={FEATURE_LABELS[f] ?? f} />
                        ))}
                      </ul>

                      <button
                        type="button"
                        disabled={isCurrent || changing !== null}
                        onClick={() => handleChange(plan.id)}
                        className={`flex items-center justify-center gap-sm rounded-lg px-md py-sm font-label-md text-label-md font-medium transition-all disabled:cursor-not-allowed ${
                          isCurrent
                            ? "border border-outline-variant/30 bg-surface-bright text-on-surface-variant"
                            : "bg-primary text-on-primary hover:bg-primary-fixed"
                        }`}
                      >
                        {changing === plan.id ? (
                          <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary/40 border-t-on-primary" />
                        ) : isCurrent ? (
                          "Active plan"
                        ) : plan.id === "enterprise" ? (
                          "Contact sales"
                        ) : (
                          `Switch to ${plan.name}`
                        )}
                      </button>
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

function limitText(value: number, suffix: string): string {
  return value < 0 ? `Unlimited ${suffix}` : `${value.toLocaleString()} ${suffix}`;
}

function PlanLine({ text }: { text: string }) {
  return (
    <li className="flex items-center gap-sm font-body-md text-[13px] text-on-surface-variant">
      <MaterialIcon name="check" size={16} className="text-primary-fixed-dim" />
      {text}
    </li>
  );
}
