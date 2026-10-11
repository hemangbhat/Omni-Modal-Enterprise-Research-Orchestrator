"use client";

import { useCallback, useEffect, useState } from "react";
import QRCode from "qrcode";
import { MaterialIcon } from "@/components/material-icon";
import { getStoredEmail } from "@/lib/auth";
import { mfaConfirm, mfaDisable, mfaEnroll, mfaStatus } from "@/lib/auth-api";

type Enrolment = { secret: string; qrDataUrl: string };
// Steps of turning two-step sign-in on. "idle" shows only the start button.
type Step = "idle" | "password" | "scan";

const CODE_INPUT_CLASS =
  "block w-40 rounded-lg border border-outline-variant/30 bg-surface-container px-md py-sm text-center font-mono-sm text-[16px] tracking-[0.3em] text-on-surface placeholder:text-on-surface-variant/30 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50";
const TEXT_INPUT_CLASS =
  "block w-full max-w-sm rounded-lg border border-outline-variant/30 bg-surface-container px-md py-sm font-body-md text-body-md text-on-surface focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50";
const LABEL_CLASS = "font-label-md text-label-md text-on-surface";
const PRIMARY_BUTTON_CLASS =
  "flex items-center gap-2 rounded border border-primary/40 bg-primary/10 px-4 py-2 font-label-md text-label-md text-primary transition-colors hover:bg-primary/20 disabled:opacity-60";
const QUIET_BUTTON_CLASS =
  "rounded border border-outline-variant/40 px-4 py-2 font-label-md text-label-md text-on-surface-variant transition-colors hover:bg-surface-variant disabled:opacity-60";

/**
 * Two-step sign-in (TOTP) management for the signed-in user. Turning it on
 * asks for the account password, then shows a QR code to scan and asks for a
 * code to confirm. Turning it off needs a current code.
 */
export function MfaSettings() {
  const [enrolled, setEnrolled] = useState<boolean | null>(null);
  const [step, setStep] = useState<Step>("idle");
  const [enrolment, setEnrolment] = useState<Enrolment | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadStatus = useCallback(() => {
    setError(null);
    mfaStatus()
      .then((s) => setEnrolled(s.enrolled))
      .catch((err: Error) => setError(`Could not load two-step sign-in status: ${err.message}`));
  }, []);

  useEffect(() => {
    setEmail(getStoredEmail() ?? "");
    loadStatus();
  }, [loadStatus]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function backToIdle() {
    setStep("idle");
    setEnrolment(null);
    setPassword("");
    setCode("");
    setError(null);
  }

  function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    return run(async () => {
      const { secret, otpauth_uri } = await mfaEnroll(email.trim(), password);
      const qrDataUrl = await QRCode.toDataURL(otpauth_uri, { margin: 1, width: 192 });
      setEnrolment({ secret, qrDataUrl });
      setPassword("");
      setCode("");
      setStep("scan");
    });
  }

  function confirmEnrolment(e: React.FormEvent) {
    e.preventDefault();
    return run(async () => {
      await mfaConfirm(code);
      backToIdle();
      setEnrolled(true);
      setNotice("Two-step sign-in is on. You will be asked for a code the next time you sign in.");
    });
  }

  function disable(e: React.FormEvent) {
    e.preventDefault();
    return run(async () => {
      await mfaDisable(code);
      setCode("");
      setEnrolled(false);
      setNotice("Two-step sign-in is off.");
    });
  }

  const codeInput = (id: string) => (
    <input
      id={id}
      type="text"
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9]*"
      maxLength={6}
      value={code}
      onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
      placeholder="123456"
      className={CODE_INPUT_CLASS}
    />
  );

  return (
    <section className="rounded-lg border border-outline-variant/40 bg-surface-container-low p-lg">
      <div className="flex items-start justify-between gap-md">
        <div>
          <h2 className="font-headline-md text-headline-md text-on-surface">Two-step sign-in</h2>
          <p className="mt-1 max-w-xl font-body-md text-[14px] text-on-surface-variant">
            Ask for a 6-digit code from an authenticator app (Google Authenticator, Authy,
            1Password) after your password. Keep the app safe: without it you cannot sign in, and
            there are no backup codes yet.
          </p>
        </div>
        {enrolled !== null ? (
          <span
            className={`flex items-center gap-1.5 rounded border px-2 py-1 font-mono-sm text-[11px] ${
              enrolled
                ? "border-emerald-400/40 text-emerald-400"
                : "border-outline-variant text-on-surface-variant"
            }`}
          >
            <MaterialIcon name={enrolled ? "verified_user" : "shield"} size={14} />
            <span>{enrolled ? "On" : "Off"}</span>
          </span>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="mt-md rounded border border-error/30 bg-error-container/20 px-md py-sm font-body-md text-[14px] text-error">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="mt-md rounded border border-emerald-400/30 bg-emerald-400/10 px-md py-sm font-body-md text-[14px] text-emerald-400">
          {notice}
        </p>
      ) : null}

      <div className="mt-lg">
        {enrolled === null ? (
          error ? (
            <button type="button" onClick={loadStatus} className={QUIET_BUTTON_CLASS}>
              Try again
            </button>
          ) : (
            <p className="font-body-md text-[14px] text-on-surface-variant">Loading…</p>
          )
        ) : null}

        {enrolled === false && step === "idle" ? (
          <button type="button" onClick={() => setStep("password")} className={PRIMARY_BUTTON_CLASS}>
            <MaterialIcon name="qr_code_2" size={18} /> Turn on two-step sign-in
          </button>
        ) : null}

        {enrolled === false && step === "password" ? (
          <form onSubmit={submitPassword} className="flex flex-col gap-md">
            <p className="font-body-md text-[14px] text-on-surface-variant">
              Confirm it is you before adding an authenticator.
            </p>
            <label htmlFor="mfa-email" className={LABEL_CLASS}>
              Email
            </label>
            <input
              id="mfa-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              className={TEXT_INPUT_CLASS}
            />
            <label htmlFor="mfa-password" className={LABEL_CLASS}>
              Current password
            </label>
            <input
              id="mfa-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className={TEXT_INPUT_CLASS}
            />
            <div className="flex gap-md">
              <button type="submit" disabled={busy || !email || !password} className={PRIMARY_BUTTON_CLASS}>
                Continue
              </button>
              <button type="button" disabled={busy} onClick={backToIdle} className={QUIET_BUTTON_CLASS}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}

        {enrolled === false && step === "scan" && enrolment ? (
          <form onSubmit={confirmEnrolment} className="flex flex-col gap-lg md:flex-row">
            {/* eslint-disable-next-line @next/next/no-img-element -- data URL generated in the browser */}
            <img
              src={enrolment.qrDataUrl}
              alt="QR code to scan with your authenticator app"
              width={192}
              height={192}
              className="h-48 w-48 rounded bg-white p-1"
            />
            <div className="flex flex-col gap-md">
              <ol className="list-decimal space-y-1 pl-5 font-body-md text-[14px] text-on-surface-variant">
                <li>Scan the QR code with your authenticator app.</li>
                <li>
                  Or enter this key by hand:{" "}
                  <code className="break-all rounded bg-surface-container px-1 font-mono-sm text-[12px] text-primary-fixed-dim">
                    {enrolment.secret}
                  </code>
                </li>
                <li>Type the 6-digit code the app shows.</li>
              </ol>
              <label htmlFor="mfa-confirm-code" className={LABEL_CLASS}>
                Code from the app
              </label>
              {codeInput("mfa-confirm-code")}
              <div className="flex gap-md">
                <button type="submit" disabled={busy || code.length !== 6} className={PRIMARY_BUTTON_CLASS}>
                  Confirm and turn on
                </button>
                <button type="button" disabled={busy} onClick={backToIdle} className={QUIET_BUTTON_CLASS}>
                  Cancel
                </button>
              </div>
            </div>
          </form>
        ) : null}

        {enrolled === true ? (
          <form onSubmit={disable} className="flex flex-col gap-md">
            <label htmlFor="mfa-disable-code" className={LABEL_CLASS}>
              To turn it off, enter a current code from your app
            </label>
            <div className="flex flex-wrap items-center gap-md">
              {codeInput("mfa-disable-code")}
              <button type="submit" disabled={busy || code.length !== 6} className={QUIET_BUTTON_CLASS}>
                Turn off
              </button>
            </div>
          </form>
        ) : null}
      </div>
    </section>
  );
}
