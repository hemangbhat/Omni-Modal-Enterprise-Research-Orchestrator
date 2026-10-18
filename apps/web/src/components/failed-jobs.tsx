"use client";

import { useCallback, useEffect, useState } from "react";
import { stageLabel } from "@/lib/documents-api";
import { getFailures, retryFailure, type Failure } from "@/lib/workspace-api";

/** Documents whose processing failed, with the reason and a retry, for admins. */
export function FailedJobs() {
  const [failures, setFailures] = useState<Failure[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [retried, setRetried] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      setFailures((await getFailures()).failures);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed jobs could not be loaded.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function retry(documentId: string) {
    setRetrying(documentId);
    try {
      await retryFailure(documentId);
      setRetried((s) => new Set(s).add(documentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The retry could not be started.");
    } finally {
      setRetrying(null);
    }
  }

  return (
    <section aria-labelledby="failed-jobs" className="mt-xl flex flex-col gap-sm">
      <div className="flex items-baseline justify-between">
        <h3 id="failed-jobs" className="text-[19px] font-semibold text-on-surface">Failed processing</h3>
        <button type="button" onClick={() => void load()} className="text-[14px] text-primary-fixed-dim hover:underline">
          Refresh
        </button>
      </div>
      {error ? <p role="alert" className="text-[14px] text-error">{error}</p> : null}
      {failures === null ? (
        <p className="text-[14px] text-on-surface-variant">Loading…</p>
      ) : failures.length === 0 ? (
        <p className="text-[14px] text-on-surface-variant">No documents have failed processing.</p>
      ) : (
        <ul className="divide-y divide-outline-variant/20 rounded-xl border border-outline-variant/30">
          {failures.map(({ document, job }) => (
            <li key={document.id} className="flex flex-col gap-sm px-md py-sm sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="truncate text-[15px] text-on-surface">{document.title}</p>
                <p className="text-[13px] text-error">{document.error_message ?? job?.error_message ?? "No reason recorded."}</p>
                <p className="text-[12px] text-on-surface-variant">
                  {job ? `${stageLabel(job.stage)} after ${job.attempts} attempt${job.attempts === 1 ? "" : "s"}` : "No job record"}
                </p>
              </div>
              {retried.has(document.id) ? (
                <span className="shrink-0 text-[13px] text-primary-fixed-dim" role="status">Retry started</span>
              ) : (
                <button
                  type="button"
                  disabled={retrying === document.id}
                  onClick={() => void retry(document.id)}
                  className="shrink-0 rounded-lg border border-outline-variant/40 px-md py-xs text-[14px] text-on-surface hover:bg-surface-container disabled:opacity-50"
                >
                  {retrying === document.id ? "Starting…" : "Retry"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
