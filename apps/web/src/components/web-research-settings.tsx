"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { useClaims } from "@/lib/use-claims";
import { getResearchSettings, setExternalResearch, type ResearchSettings } from "@/lib/workspace-api";

/**
 * Whether questions the workspace's documents cannot answer may be searched on
 * the web. Off by default. Shows plainly what leaves the system and why a search
 * might not run (no provider, plan, monthly allowance).
 */
export function WebResearchSettings() {
  const claims = useClaims();
  const isAdmin = claims?.roles.includes("admin") ?? false;
  const [settings, setSettings] = useState<ResearchSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getResearchSettings()
      .then(setSettings)
      .catch((err) => setError(err instanceof Error ? err.message : "Settings could not be loaded."));
  }, []);

  async function toggle(enabled: boolean) {
    setSaving(true);
    setError(null);
    try {
      setSettings(await setExternalResearch(enabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The setting could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="web-research-heading" className="flex flex-col gap-md rounded-2xl border border-outline-variant/30 bg-surface-container-low p-lg">
      <div className="flex items-start gap-md">
        <MaterialIcon name="public" size={22} className="mt-[2px] text-primary-fixed-dim" />
        <div className="flex min-w-0 flex-1 flex-col gap-xs">
          <h2 id="web-research-heading" className="text-[18px] font-semibold text-on-surface">
            Search the web when your documents don&apos;t have the answer
          </h2>
          <p className="text-[14px] leading-relaxed text-on-surface-variant">
            When a question can&apos;t be answered from this workspace&apos;s documents, OMERO can search the web and
            show what it finds, clearly marked &ldquo;From the web&rdquo; with links to each page.
          </p>
          <ul className="mt-xs list-disc space-y-[2px] pl-lg text-[14px] text-on-surface-variant">
            <li>Only the question is sent, never document text.</li>
            <li>Email addresses, phone numbers, links, long numbers and any wording or figures taken from your documents are removed first.</li>
            <li>Each answer shows exactly what was searched for.</li>
          </ul>
        </div>
      </div>

      {error ? (
        <p role="alert" className="text-[14px] text-error">{error}</p>
      ) : !settings ? (
        <p className="text-[14px] text-on-surface-variant" role="status">Loading…</p>
      ) : (
        <>
          <label className={`flex items-center gap-md ${isAdmin ? "cursor-pointer" : "cursor-not-allowed opacity-70"}`}>
            <input
              type="checkbox"
              role="switch"
              checked={settings.external_research_enabled}
              disabled={!isAdmin || saving}
              onChange={(e) => void toggle(e.target.checked)}
              className="h-5 w-5 accent-[#E3B341] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-fixed-dim"
            />
            <span className="text-[15px] text-on-surface">
              {settings.external_research_enabled ? "On for this workspace" : "Off for this workspace"}
            </span>
          </label>
          {!isAdmin ? <p className="text-[13px] text-on-surface-variant">Only an admin can change this.</p> : null}

          <dl className="grid grid-cols-1 gap-sm text-[14px] sm:grid-cols-3">
            <Fact label="Search provider" value={settings.provider_configured ? "Connected" : "Not set up on the server"} ok={settings.provider_configured} />
            <Fact
              label="Your plan"
              value={settings.plan_includes ? "Includes web research" : "Doesn't include web research"}
              ok={settings.plan_includes}
            />
            <Fact
              label="This month"
              value={
                settings.monthly_limit < 0
                  ? `${settings.used_this_month} searches (no limit)`
                  : `${settings.used_this_month} of ${settings.monthly_limit} searches`
              }
              ok={settings.monthly_limit < 0 || settings.used_this_month < settings.monthly_limit}
            />
          </dl>
          {!settings.plan_includes ? (
            <p className="text-[13px] text-on-surface-variant">
              Web research is part of the Pro plan. <Link href="/pricing" className="text-primary-fixed-dim underline">Compare plans</Link>
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function Fact({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="rounded-xl border border-outline-variant/20 p-sm">
      <dt className="text-[12px] tracking-wide text-on-surface-variant/80">{label}</dt>
      <dd className="mt-[2px] flex items-center gap-xs text-on-surface">
        <MaterialIcon name={ok ? "check_circle" : "info"} size={16} className={ok ? "text-primary-fixed-dim" : "text-on-surface-variant"} />
        {value}
      </dd>
    </div>
  );
}
