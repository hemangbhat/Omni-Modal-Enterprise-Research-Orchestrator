import type { ReactNode } from "react";
import { Wordmark } from "@/components/wordmark";

/** Shared class names for the signed-out account screens. */
export const AUTH_INPUT_CLASS =
  "block w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-md py-sm font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/40 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50";
export const AUTH_SUBMIT_CLASS =
  "flex w-full items-center justify-center gap-sm rounded-xl bg-primary-fixed-dim hover:bg-primary px-lg py-md font-label-md text-label-md font-semibold text-on-primary-container transition-all disabled:opacity-60";
export const AUTH_LINK_CLASS = "text-primary-fixed-dim underline hover:text-primary-fixed";

/** Logo plus card frame used by the signed-out account screens. */
export function AuthShell({
  title,
  intro,
  children
}: {
  title: string;
  intro: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="grid min-h-screen bg-surface lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section className="hidden flex-col justify-between border-r border-outline-variant/40 bg-surface-container-lowest p-xl lg:flex">
        <Wordmark size="lg" />
        <div className="max-w-[30rem]">
          <p className="font-serif text-[40px] font-semibold leading-[1.1] tracking-[-0.02em] text-on-surface">
            Answers that show where they came from.
          </p>
          <p className="mt-lg text-[16px] leading-relaxed text-on-surface-variant">
            Upload reports, contracts and meeting recordings, then ask in plain words. Every answer quotes the page or
            the moment in the recording it relies on, and says so when your documents don&apos;t hold the answer.
          </p>
        </div>
        <p className="text-[13px] text-on-surface-variant">
          Each workspace&apos;s documents are kept apart from every other workspace.
        </p>
      </section>
      <div className="flex flex-col items-center justify-center px-lg py-xl">
        <div className="w-full max-w-md">
          <div className="mb-xl lg:hidden">
            <Wordmark />
          </div>
          <h1 className="mb-sm text-[30px] leading-tight text-on-surface">{title}</h1>
          <div className="mb-xl font-body-md text-body-md text-on-surface-variant">{intro}</div>
          {children}
        </div>
      </div>
    </main>
  );
}

export function AuthMessage({ kind, children }: { kind: "error" | "success"; children: ReactNode }) {
  const tone =
    kind === "error"
      ? "border-error/30 bg-error-container/20 text-error"
      : "border-emerald-400/30 bg-emerald-400/10 text-emerald-400";
  return (
    <div
      role={kind === "error" ? "alert" : "status"}
      className={`mb-lg rounded-xl border px-lg py-md font-body-md text-[14px] ${tone}`}
    >
      {children}
    </div>
  );
}
