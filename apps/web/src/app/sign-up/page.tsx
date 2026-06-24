"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { useAuth } from "@/components/auth-context";
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
      signIn(result.token);
      router.replace("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-surface px-lg">
      <div className="w-full max-w-md">
        <div className="mb-xl flex items-center justify-center gap-md">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-fixed-dim text-on-primary-fixed shadow-[0_0_20px_rgba(0,218,243,0.3)]">
            <MaterialIcon name="science" size={22} fill />
          </span>
          <div>
            <h1 className="font-headline-md text-headline-md font-black tracking-tight text-on-surface">
              OMERO
            </h1>
            <p className="font-mono-sm text-mono-sm text-primary-fixed-dim/80">AI Research Platform</p>
          </div>
        </div>

        <div className="premium-card rounded-2xl p-xl">
          <h2 className="mb-sm font-headline-md text-headline-md text-on-surface">Create your account</h2>
          <p className="mb-xl font-body-md text-body-md text-on-surface-variant">
            Sign up to provision your organization. Your password is hashed (PBKDF2) and never stored in plain text.
          </p>

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
              className="flex w-full items-center justify-center gap-sm rounded-xl bg-gradient-to-r from-primary-container to-primary px-lg py-md font-label-md text-label-md font-semibold text-on-primary-container shadow-[0_4px_14px_rgba(0,218,243,0.2)] transition-all hover:-translate-y-0.5 hover:shadow-[0_6px_20px_rgba(0,218,243,0.3)] disabled:opacity-60"
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

          <div className="mt-xl flex items-center justify-center gap-sm text-on-surface-variant">
            <MaterialIcon name="security" size={14} />
            <span className="font-mono-sm text-[11px]">PBKDF2-hashed · JWT HS256 · Tenant-isolated</span>
          </div>
        </div>
      </div>
    </main>
  );
}
