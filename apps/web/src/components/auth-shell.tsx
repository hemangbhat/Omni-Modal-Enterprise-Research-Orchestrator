import type { ReactNode } from "react";
import { MaterialIcon } from "@/components/material-icon";

/** Shared class names for the signed-out account screens. */
export const AUTH_INPUT_CLASS =
  "block w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-md py-sm font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/40 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50";
export const AUTH_SUBMIT_CLASS =
  "flex w-full items-center justify-center gap-sm rounded-xl bg-gradient-to-r from-primary-container to-primary px-lg py-md font-label-md text-label-md font-semibold text-on-primary-container shadow-[0_4px_14px_rgba(0,218,243,0.2)] transition-all hover:-translate-y-0.5 hover:shadow-[0_6px_20px_rgba(0,218,243,0.3)] disabled:opacity-60";
export const AUTH_LINK_CLASS = "text-primary-fixed-dim underline hover:text-primary-fixed";

/** Logo plus card frame used by the signed-out account screens. */
export function AuthShell({
  title,
  intro,
  children
}: {
  title: string;
  intro: string;
  children: ReactNode;
}) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-surface px-lg">
      <div className="w-full max-w-md">
        <div className="mb-xl flex items-center justify-center gap-md">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-fixed-dim text-on-primary-fixed shadow-[0_0_20px_rgba(0,218,243,0.3)]">
            <MaterialIcon name="science" size={22} fill />
          </span>
          <div>
            <p className="font-headline-md text-headline-md font-black tracking-tight text-on-surface">
              OMERO
            </p>
            <p className="font-mono-sm text-mono-sm text-primary-fixed-dim/80">AI Research Platform</p>
          </div>
        </div>
        <div className="premium-card rounded-2xl p-xl">
          <h1 className="mb-sm font-headline-md text-headline-md text-on-surface">{title}</h1>
          <p className="mb-xl font-body-md text-body-md text-on-surface-variant">{intro}</p>
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
