"use client";

import { MaterialIcon } from "@/components/material-icon";

/**
 * Sign-in page — this project uses static bearer token authentication
 * for local development and portfolio demos. In a production deployment
 * this would integrate with an IdP (NextAuth, Auth0, etc.).
 *
 * To generate a token for this demo:
 *   python scripts/issue_jwt.py --tenant demo-tenant --user u1 --roles researcher,admin
 * Then set NEXT_PUBLIC_API_TOKEN in apps/web/.env.local.
 */
export default function SignInPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-surface px-lg">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="mb-xl flex items-center justify-center gap-md">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-fixed-dim text-on-primary-fixed shadow-[0_0_20px_rgba(0,218,243,0.3)]">
            <MaterialIcon name="science" size={22} fill />
          </span>
          <div>
            <h1 className="font-headline-md text-headline-md font-black tracking-tight text-on-surface">OMERO</h1>
            <p className="font-mono-sm text-mono-sm text-primary-fixed-dim/80">AI Research Platform</p>
          </div>
        </div>

        {/* Card */}
        <div className="premium-card rounded-2xl p-xl">
          <h2 className="mb-sm font-headline-md text-headline-md text-on-surface">Authentication</h2>
          <p className="mb-xl font-body-md text-body-md text-on-surface-variant">
            This demo uses static bearer token authentication. No password is required.
          </p>

          <div className="space-y-lg">
            <div className="rounded-xl border border-primary-container/20 bg-primary-container/5 p-lg">
              <div className="mb-sm flex items-center gap-sm">
                <MaterialIcon name="info" size={16} className="text-primary-fixed-dim" />
                <span className="font-label-md text-label-md text-primary-fixed-dim">Developer / Demo Mode</span>
              </div>
              <p className="font-body-md text-[14px] text-on-surface-variant">
                To access the platform, generate a bearer token using the CLI:
              </p>
              <pre className="mt-sm overflow-x-auto rounded border border-outline-variant/20 bg-surface-container-lowest px-md py-sm font-mono-sm text-[11px] text-on-surface">
{`python scripts/issue_jwt.py \\
  --tenant demo-tenant \\
  --user u1 \\
  --roles researcher,admin`}
              </pre>
              <p className="mt-sm font-body-md text-[14px] text-on-surface-variant">
                Then add the token to <code className="rounded bg-surface-container px-1 font-mono-sm text-[12px] text-primary-fixed-dim">apps/web/.env.local</code>:
              </p>
              <pre className="mt-sm overflow-x-auto rounded border border-outline-variant/20 bg-surface-container-lowest px-md py-sm font-mono-sm text-[11px] text-on-surface">
{`NEXT_PUBLIC_API_TOKEN=<your-token-here>`}
              </pre>
            </div>

            <div className="rounded-xl border border-outline-variant/20 bg-surface-container-low p-lg">
              <div className="mb-sm flex items-center gap-sm">
                <MaterialIcon name="lock" size={16} className="text-on-surface-variant" />
                <span className="font-label-md text-label-md text-on-surface-variant">Production Authentication</span>
              </div>
              <p className="font-body-md text-[14px] text-on-surface-variant">
                In a production deployment, this page would integrate with an identity provider
                (e.g. NextAuth.js, Auth0, or Okta). The backend verifies JWT tokens using
                HS256 with a shared secret.
              </p>
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
