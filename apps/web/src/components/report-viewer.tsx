"use client";

import { useEffect, useRef } from "react";
import { AnswerMarkdown } from "@/components/answer-markdown";
import { MaterialIcon } from "@/components/material-icon";
import { saveReportFile, type Report } from "@/lib/projects-api";

/** A generated report, readable in place and downloadable as Markdown. */
export function ReportViewer({ report, onClose }: { report: Report; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[70] flex justify-center bg-black/60 p-md backdrop-blur-sm md:p-xl" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={report.title}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-outline-variant/40 bg-surface-container-low shadow-2xl"
      >
        <div className="flex items-center justify-between gap-md border-b border-outline-variant/20 px-lg py-md">
          <h2 className="truncate text-[16px] font-semibold text-on-surface">{report.title}</h2>
          <div className="flex shrink-0 gap-xs">
            <button
              type="button"
              onClick={() => saveReportFile(report)}
              className="flex items-center gap-xs rounded-lg border border-outline-variant/40 px-md py-xs text-[13px] text-on-surface hover:bg-surface-container"
            >
              <MaterialIcon name="download" size={16} /> Download .md
            </button>
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="Close report"
              className="rounded-lg p-xs text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
            >
              <MaterialIcon name="close" size={18} />
            </button>
          </div>
        </div>
        <div className="overflow-y-auto px-lg py-lg">
          <AnswerMarkdown markdown={report.markdown} citationCount={0} />
        </div>
      </div>
    </div>
  );
}
