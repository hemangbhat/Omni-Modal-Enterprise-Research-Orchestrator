"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MaterialIcon } from "@/components/material-icon";
import { useAuth } from "@/components/auth-context";
import { acceptInvite, previewInvite, type InvitePreview } from "@/lib/saas-api";

const PENDING_KEY = "omni_pending_invite";

export default function AcceptInvitePage() {
  const router = useRouter();
  const { isReady, isAuthenticated } = useAuth();

  const [token, setToken] = useState<string | null>(null);
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [accepted, setAccepted] = useState(false);

  // Read the token from the URL (and remember it across a sign-in round trip).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = params.get("token") ?? window.localStorage.getItem(PENDING_KEY);
    if (t) {
      setToken(t);
      window.localStorage.setItem(PENDING_KEY, t);
    }
  }, []);

  // Once authenticated, load the invite preview.
  useEffect(() => {
    if (!isReady) return;
    if (!token) {
      setLoading(false);
      return;
    }
    if (!isAuthenticated) {
      setLoading(false);
      return;
    }
    setLoading(true);
    previewInvite(token)
      .then((p) => {
        setPreview(p);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [isReady, isAuthenticated, token]);

  async function handleAccept() {
    if (!token) return;
    setAccepting(true);
    setError(null);
    try {
      await acceptInvite(token);
      window.localStorage.removeItem(PENDING_KEY);
      setAccepted(true);
      setTimeout(() => router.replace("/"), 1500);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAccepting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-lg">
      <div className="w-full max-w-md rounded-2xl border border-outline-variant/30 bg-surface-container-low p-xl shadow-[0_12px_40px_rgba(0,0,0,0.4)]">
        <div className="mb-lg flex items-center gap-md">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-fixed-dim text-on-primary-fixed">
            <MaterialIcon name="group_add" size={20} fill />
          </span>
          <div>
            <h1 className="font-headline-md text-headline-md font-bold text-on-surface">
              Team invitation
            </h1>
            <p className="font-mono-sm text-[11px] text-primary-fixed-dim/80">OMERO</p>
          </div>
        </div>

        {!isReady || loading ? (
          <div className="flex items-center justify-center py-lg">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
          </div>
        ) : !token ? (
          <Message
            icon="link_off"
            tone="error"
            title="Missing invitation token"
            body="This invitation link is incomplete. Ask your admin to resend the invite."
          />
        ) : accepted ? (
          <Message
            icon="check_circle"
            tone="success"
            title="You're in!"
            body="Invitation accepted. Taking you to your dashboard…"
          />
        ) : !isAuthenticated ? (
          <div className="flex flex-col gap-md">
            <Message
              icon="lock"
              tone="info"
              title="Sign in to accept"
              body="You need to sign in before joining this organization. Your invitation will be waiting."
            />
            <Link
              href="/sign-in"
              className="flex items-center justify-center gap-sm rounded-lg bg-primary px-md py-sm font-label-md text-label-md font-medium text-on-primary transition-all hover:bg-primary-fixed"
            >
              <MaterialIcon name="login" size={18} />
              Go to sign-in
            </Link>
          </div>
        ) : error ? (
          <Message icon="error_outline" tone="error" title="Could not load invitation" body={error} />
        ) : preview && !preview.valid ? (
          <Message
            icon="schedule"
            tone="error"
            title={preview.expired ? "Invitation expired" : "Invitation unavailable"}
            body="This invitation is no longer valid. Ask your admin to send a new one."
          />
        ) : preview ? (
          <div className="flex flex-col gap-lg">
            <div className="rounded-xl border border-outline-variant/20 bg-surface-container p-md">
              <p className="font-body-md text-[13px] text-on-surface-variant">
                You&apos;ve been invited to join
              </p>
              <p className="font-headline-md text-headline-md font-bold text-on-surface">
                {preview.organization}
              </p>
              <div className="mt-sm flex items-center gap-sm">
                <span className="font-body-md text-[13px] text-on-surface-variant">{preview.email}</span>
                <span className="rounded border border-outline-variant/20 bg-surface-bright px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-primary-fixed-dim">
                  {preview.role}
                </span>
              </div>
            </div>
            {error ? (
              <p className="font-body-md text-[13px] text-error">{error}</p>
            ) : null}
            <button
              type="button"
              onClick={handleAccept}
              disabled={accepting}
              className="flex items-center justify-center gap-sm rounded-lg bg-primary px-md py-sm font-label-md text-label-md font-medium text-on-primary transition-all hover:bg-primary-fixed disabled:opacity-60"
            >
              {accepting ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary/40 border-t-on-primary" />
              ) : (
                <MaterialIcon name="check" size={18} />
              )}
              Accept invitation
            </button>
          </div>
        ) : null}
      </div>
    </main>
  );
}

function Message({
  icon,
  title,
  body,
  tone
}: {
  icon: string;
  title: string;
  body: string;
  tone: "info" | "success" | "error";
}) {
  const color =
    tone === "success"
      ? "text-primary-fixed-dim"
      : tone === "error"
        ? "text-error"
        : "text-amber-400";
  return (
    <div className="flex flex-col items-center gap-sm py-md text-center">
      <MaterialIcon name={icon} size={32} className={color} />
      <h2 className="font-headline-md text-headline-md text-on-surface">{title}</h2>
      <p className="font-body-md text-[14px] text-on-surface-variant">{body}</p>
    </div>
  );
}
