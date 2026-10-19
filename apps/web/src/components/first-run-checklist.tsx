"use client";

import Link from "next/link";
import { useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import type { Dashboard } from "@/lib/workspace-api";

type Progress = NonNullable<Dashboard["progress"]>;

const STEPS: { key: keyof Progress; title: string; text: string; href: string; action: string }[] = [
  { key: "uploaded", title: "Upload a document", text: "PDFs and recordings are read, split into passages and indexed.", href: "/upload", action: "Upload" },
  { key: "asked", title: "Ask a question", text: "Answers cite the page or the moment in the recording they come from.", href: "/research", action: "Ask" },
  { key: "project", title: "Start a project", text: "Group documents, and save the answers worth keeping as findings.", href: "/projects", action: "New project" },
  { key: "report", title: "Generate a report", text: "Turn a project's findings into one Markdown report with its sources.", href: "/projects", action: "Open projects" }
];

const storageKey = (userId: string) => `omero.firstRun.dismissed.${userId}`;

function readDismissed(userId: string): boolean {
  try {
    return typeof window !== "undefined" && window.localStorage.getItem(storageKey(userId)) === "1";
  } catch {
    return false;
  }
}

/**
 * The main journey as a checklist, ticked from what the workspace has really
 * done. It stays until every step is done or the person hides it.
 */
export function FirstRunChecklist({ progress, userId }: { progress: Progress; userId: string }) {
  // Rendered only after the dashboard has loaded in the browser, so storage can be read at once.
  const [dismissed, setDismissed] = useState(() => readDismissed(userId));

  const done = STEPS.filter((s) => progress[s.key]).length;
  if (dismissed || done === STEPS.length) return null;
  const next = STEPS.find((s) => !progress[s.key]);

  function hide() {
    setDismissed(true);
    try {
      window.localStorage.setItem(storageKey(userId), "1");
    } catch {
      /* storage unavailable: hidden for this visit only */
    }
  }

  return (
    <section aria-labelledby="start-heading" className="rounded-2xl border border-outline-variant/30 bg-surface-container-low p-lg">
      <div className="mb-md flex items-start justify-between gap-md">
        <div>
          <h3 id="start-heading" className="text-[19px] font-semibold text-on-surface">Getting started</h3>
          <p className="mt-[2px] text-[14px] text-on-surface-variant">
            {done} of {STEPS.length} done. {next ? `Next: ${next.title.toLowerCase()}.` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={hide}
          className="rounded-lg px-sm py-xs text-[13px] text-on-surface-variant hover:bg-surface-container hover:text-on-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-fixed-dim"
        >
          Hide
        </button>
      </div>
      <div
        className="mb-md h-1.5 overflow-hidden rounded-full bg-surface-container-high"
        role="progressbar"
        aria-label="Getting started progress"
        aria-valuemin={0}
        aria-valuemax={STEPS.length}
        aria-valuenow={done}
      >
        <div className="h-full rounded-full bg-primary-fixed-dim transition-all" style={{ width: `${(done / STEPS.length) * 100}%` }} />
      </div>
      <ol className="grid grid-cols-1 gap-sm md:grid-cols-2 xl:grid-cols-4">
        {STEPS.map((step, i) => {
          const complete = progress[step.key];
          return (
            <li key={step.key}>
              <Link
                href={step.href}
                aria-label={`${step.title}${complete ? " (done)" : ""}`}
                className={`flex h-full flex-col gap-xs rounded-xl border p-md transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-fixed-dim ${
                  complete
                    ? "border-outline-variant/20 bg-transparent"
                    : step === next
                      ? "border-primary-fixed-dim/60 bg-primary-fixed-dim/[0.06]"
                      : "border-outline-variant/30 hover:border-primary-fixed-dim/50"
                }`}
              >
                <span className="flex items-center gap-xs text-[13px] font-medium text-primary-fixed-dim">
                  <MaterialIcon name={complete ? "check_circle" : "radio_button_unchecked"} size={16} />
                  {complete ? "Done" : `Step ${i + 1}`}
                </span>
                <span className={`text-[16px] font-semibold ${complete ? "text-on-surface-variant line-through decoration-on-surface-variant/40" : "text-on-surface"}`}>
                  {step.title}
                </span>
                <span className="text-[14px] leading-relaxed text-on-surface-variant">{step.text}</span>
                {!complete ? <span className="mt-auto pt-xs text-[13px] font-medium text-primary-fixed-dim">{step.action} →</span> : null}
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
