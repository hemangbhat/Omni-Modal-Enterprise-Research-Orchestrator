"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/auth-shell";
import { MaterialIcon } from "@/components/material-icon";
import { useAuth } from "@/components/auth-context";
import { setStoredEmail } from "@/lib/auth";
import { register } from "@/lib/auth-api";

/**
 * Sign-up page. Creates a real credential account via `POST /auth/register`
 * (PBKDF2-hashed password stored server-side) and starts a session with the
 * returned backend JWT. A new account becomes the admin owner of a freshly
 * provisioned organization/tenant.
 */
export default function SignUpPage() {
  const router = useRouter();
  const { signIn, isAuthenticated, isReady } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (isReady && isAuthenticated) {
      router.replace("/");
    }
  }, [isReady, isAuthenticated, router]);

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    if (!email.includes("@")) {
      setError("Enter a valid email address.");
      return;
    }
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
      const result = await register(email.trim(), password);
      setStoredEmail(result.email);
      signIn(result.token, result.refresh_token);
      router.replace("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell title="Create your account" intro="A new workspace starts empty and private to your team. You can invite colleagues once you're in.">

          {error ? (
            <div className="mb-lg rounded-xl border border-error/30 bg-error-container/20 px-lg py-md font-body-md text-[14px] text-error">
              {error}
            </div>
          ) : null}

          <form onSubmit={handleRegister} className="space-y-md">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="email"
              className="block w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-md py-sm font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/40 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password (min 8 characters)"
              autoComplete="new-password"
              className="block w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-md py-sm font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/40 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50"
            />
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Confirm password"
              autoComplete="new-password"
              className="block w-full rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-md py-sm font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/40 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50"
            />
            <button
              type="submit"
              disabled={submitting}
              className="flex w-full items-center justify-center gap-sm rounded-xl bg-primary-fixed-dim hover:bg-primary px-lg py-md font-label-md text-label-md font-semibold text-on-primary-container transition-all disabled:opacity-60"
            >
              {submitting ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary-container/40 border-t-on-primary-container" />
              ) : (
                <MaterialIcon name="person_add" size={18} />
              )}
              Create account
            </button>
          </form>

          <p className="mt-lg text-center font-body-md text-[13px] text-on-surface-variant">
            Already have an account?{" "}
            <Link href="/sign-in" className="text-primary-fixed-dim underline hover:text-primary-fixed">
              Sign in
            </Link>
          </p>

    </AuthShell>
  );
}
