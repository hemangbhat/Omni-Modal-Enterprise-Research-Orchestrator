"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { useAuth } from "@/components/auth-context";
import { getDemoToken, isTokenExpired } from "@/lib/auth";
import { login } from "@/lib/auth-api";

/**
 * Sign-in page.
 *
 * The backend authenticates requests with a JWT bearer token (HS256). This
 * page lets you start a session either by:
 *   1. One click using the demo token shipped via NEXT_PUBLIC_API_TOKEN, or
 *   2. Pasting a token minted with `python scripts/issue_jwt.py ...`.
 *
 * The chosen token is stored in the browser session and sent on every API
 * call. Signing out clears it. In production this page would integrate with
 * an identity provider (NextAuth, Auth0, Okta) instead of accepting a raw
 * token.
 */
export default function SignInPage() {
  const router = useRouter();
  const { signIn, isAuthenticated, isReady } = useAuth();
  const [tokenInput, setTokenInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const demoToken = getDemoToken();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // If already signed in, don't show the form — go to the dashboard.
  useEffect(() => {
    if (isReady && isAuthenticated) {
      router.replace("/");
    }
  }, [isReady, isAuthenticated, router]);

  async function handleCredentialLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!email.includes("@") || password.length < 1) {
      setError("Enter your email and password.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await login(email.trim(), password);
      signIn(result.token);
      router.replace("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  function applyToken(token: string) {
    const trimmed = token.trim();
    if (!trimmed) {
      setError("Enter a bearer token to continue.");
      return;
    }
    if (trimmed.split(".").length !== 3) {
      setError("That doesn't look like a JWT (expected three dot-separated parts).");
      return;
    }
    if (isTokenExpired(trimmed)) {
      setError("This token has expired. Generate a fresh one with scripts/issue_jwt.py.");
      return;
    }
    setError(null);
    signIn(trimmed);
    router.replace("/");
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-surface px-lg">
      <div className="w-full max-w-md">
        {/* Logo */}
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

        {/* Card */}
        <div className="premium-card rounded-2xl p-xl">
          <h2 className="mb-sm font-headline-md text-headline-md text-on-surface">Sign in</h2>
          <p className="mb-xl font-body-md text-body-md text-on-surface-variant">
            Start a session with a JWT bearer token. The backend verifies it with HS256.
          </p>

          {error ? (
            <div className="mb-lg rounded-xl border border-error/30 bg-error-container/20 px-lg py-md font-body-md text-[14px] text-error">
              {error}
            </div>
          ) : null}

          <div className="space-y-lg">
            {/* Email + password sign in (real credential auth) */}
            <form onSubmit={handleCredentialLogin} className="space-y-md">
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
                placeholder="Password"
                autoComplete="current-password"
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
                  <MaterialIcon name="login" size={18} />
                )}
                Sign in
              </button>
              <p className="text-center font-body-md text-[13px] text-on-surface-variant">
                New here?{" "}
                <Link href="/sign-up" className="text-primary-fixed-dim underline hover:text-primary-fixed">
                  Create an account
                </Link>
              </p>
            </form>

            <div className="flex items-center gap-md">
              <span className="h-px flex-1 bg-outline-variant/20" />
              <span className="font-mono-sm text-[11px] uppercase tracking-widest text-on-surface-variant/60">
                or use a token
              </span>
              <span className="h-px flex-1 bg-outline-variant/20" />
            </div>

            {/* One-click demo sign in */}
            {demoToken ? (
              <button
                type="button"
                onClick={() => applyToken(demoToken)}
                className="flex w-full items-center justify-center gap-sm rounded-xl bg-gradient-to-r from-primary-container to-primary px-lg py-md font-label-md text-label-md font-semibold text-on-primary-container shadow-[0_4px_14px_rgba(0,218,243,0.2)] transition-all hover:-translate-y-0.5 hover:shadow-[0_6px_20px_rgba(0,218,243,0.3)]"
              >
                <MaterialIcon name="bolt" size={18} />
                Continue with demo token
              </button>
            ) : (
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-lg">
                <div className="mb-sm flex items-center gap-sm">
                  <MaterialIcon name="warning" size={16} className="text-amber-400" />
                  <span className="font-label-md text-label-md text-amber-400">No demo token configured</span>
                </div>
                <p className="font-body-md text-[14px] text-on-surface-variant">
                  Set <code className="rounded bg-surface-container px-1 font-mono-sm text-[12px] text-primary-fixed-dim">NEXT_PUBLIC_API_TOKEN</code>{" "}
                  in <code className="rounded bg-surface-container px-1 font-mono-sm text-[12px] text-primary-fixed-dim">apps/web/.env.local</code> to enable one-click sign-in, or paste a token below.
                </p>
              </div>
            )}

            <div className="flex items-center gap-md">
              <span className="h-px flex-1 bg-outline-variant/20" />
              <span className="font-mono-sm text-[11px] uppercase tracking-widest text-on-surface-variant/60">
                or paste a token
              </span>
              <span className="h-px flex-1 bg-outline-variant/20" />
            </div>

            {/* Manual token entry */}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                applyToken(tokenInput);
              }}
              className="space-y-md"
            >
              <textarea
                value={tokenInput}
                onChange={(event) => setTokenInput(event.target.value)}
                rows={3}
                placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…"
                className="block w-full resize-none rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-md py-sm font-mono-sm text-[12px] text-on-surface placeholder:text-on-surface-variant/40 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50"
              />
              <button
                type="submit"
                className="flex w-full items-center justify-center gap-sm rounded-xl border border-outline-variant/30 bg-surface-container-high px-lg py-md font-label-md text-label-md text-on-surface transition-all hover:border-primary-fixed-dim/50"
              >
                <MaterialIcon name="login" size={18} />
                Sign in with token
              </button>
            </form>

            <div className="rounded-xl border border-outline-variant/20 bg-surface-container-low p-lg">
              <div className="mb-sm flex items-center gap-sm">
                <MaterialIcon name="terminal" size={16} className="text-on-surface-variant" />
                <span className="font-label-md text-label-md text-on-surface-variant">Generate a token</span>
              </div>
              <pre className="overflow-x-auto rounded border border-outline-variant/20 bg-surface-container-lowest px-md py-sm font-mono-sm text-[11px] text-on-surface">
{`python scripts/issue_jwt.py \\
  --tenant demo-tenant \\
  --user u1 \\
  --roles researcher,admin`}
              </pre>
            </div>
          </div>

          <div className="mt-xl flex items-center justify-center gap-sm text-on-surface-variant">
            <MaterialIcon name="security" size={14} />
            <span className="font-mono-sm text-[11px]">
              JWT HS256 · RBAC enforced · Tenant-isolated · Rate-limited
            </span>
          </div>
        </div>
      </div>
    </main>
  );
}
