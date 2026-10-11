"use client";

import Link from "next/link";
import { useState } from "react";
import {
  AUTH_INPUT_CLASS,
  AUTH_LINK_CLASS,
  AUTH_SUBMIT_CLASS,
  AuthMessage,
  AuthShell
} from "@/components/auth-shell";
import { forgotPassword } from "@/lib/auth-api";

/**
 * Forgot-password page. Requests a reset email via `POST /auth/password/forgot`.
 * The confirmation is the same whether or not the address has an account, so
 * this screen cannot be used to find out who is registered.
 */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.includes("@")) {
      setError("Enter the email address you signed up with.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(`Could not send the request: ${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Reset your password"
      intro="Enter your account email and we will send you a link to choose a new password."
    >
      {error ? <AuthMessage kind="error">{error}</AuthMessage> : null}
      {sent ? (
        <AuthMessage kind="success">
          If an account exists for that address, a reset link is on its way. The link works once
          and expires in 30 minutes.
        </AuthMessage>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-md">
          <label htmlFor="forgot-email" className="block font-label-md text-label-md text-on-surface">
            Email
          </label>
          <input
            id="forgot-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            autoComplete="email"
            className={AUTH_INPUT_CLASS}
          />
          <button type="submit" disabled={submitting} className={AUTH_SUBMIT_CLASS}>
            {submitting ? "Sending…" : "Send reset link"}
          </button>
        </form>
      )}
      <p className="mt-lg text-center font-body-md text-[13px] text-on-surface-variant">
        <Link href="/sign-in" className={AUTH_LINK_CLASS}>
          Back to sign in
        </Link>
      </p>
    </AuthShell>
  );
}
