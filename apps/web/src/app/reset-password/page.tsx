"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  AUTH_INPUT_CLASS,
  AUTH_LINK_CLASS,
  AUTH_SUBMIT_CLASS,
  AuthMessage,
  AuthShell
} from "@/components/auth-shell";
import { resetPassword } from "@/lib/auth-api";

/**
 * Reset-password page, opened from the link in a reset email
 * (`/reset-password?token=…`). Sets a new password via
 * `POST /auth/password/reset`; the token works once and expires.
 */
export default function ResetPasswordPage() {
  // undefined until the query string has been read on the client.
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // True once the backend has rejected a submit, so we offer a fresh link.
  const [linkRefused, setLinkRefused] = useState(false);

  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token"));
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      // The backend refused the link itself (used, expired or unknown).
      setLinkRefused(true);
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  const requestNewLink = (
    <Link href="/forgot-password" className={AUTH_LINK_CLASS}>
      Request a new link
    </Link>
  );

  return (
    <AuthShell title="Choose a new password" intro="Your new password must be at least 8 characters.">
      {error ? <AuthMessage kind="error">{error}</AuthMessage> : null}

      {done ? (
        <>
          <AuthMessage kind="success">
            Your password has been changed. Other devices signed in to this account will be asked
            to sign in again within 15 minutes.
          </AuthMessage>
          <Link href="/sign-in" className={AUTH_SUBMIT_CLASS}>
            Sign in
          </Link>
        </>
      ) : token === null ? (
        <>
          <AuthMessage kind="error">This reset link is invalid or has expired.</AuthMessage>
          <p className="text-center font-body-md text-[13px] text-on-surface-variant">{requestNewLink}</p>
        </>
      ) : token === undefined ? null : (
        <>
          <form onSubmit={handleSubmit} className="space-y-md">
            <label htmlFor="reset-password" className="block font-label-md text-label-md text-on-surface">
              New password
            </label>
            <input
              id="reset-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              className={AUTH_INPUT_CLASS}
            />
            <label htmlFor="reset-confirm" className="block font-label-md text-label-md text-on-surface">
              Confirm new password
            </label>
            <input
              id="reset-confirm"
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              className={AUTH_INPUT_CLASS}
            />
            <button type="submit" disabled={submitting} className={AUTH_SUBMIT_CLASS}>
              {submitting ? "Saving…" : "Change password"}
            </button>
          </form>
          {linkRefused ? (
            <p className="mt-lg text-center font-body-md text-[13px] text-on-surface-variant">
              {requestNewLink}
            </p>
          ) : null}
        </>
      )}
    </AuthShell>
  );
}
