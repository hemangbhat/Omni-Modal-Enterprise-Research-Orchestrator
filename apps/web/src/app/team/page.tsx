"use client";

import { useEffect, useState } from "react";
import { MaterialIcon } from "@/components/material-icon";
import { TopBar } from "@/components/top-bar";
import {
  inviteMember,
  listMembers,
  PlanLimitError,
  type Invite,
  type Member
} from "@/lib/saas-api";

const ROLES = ["researcher", "admin", "auditor"];

function roleBadge(role: string) {
  return (
    <span className="rounded border border-outline-variant/20 bg-surface-container px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-primary-fixed-dim">
      {role}
    </span>
  );
}

export default function TeamPage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [role, setRole] = useState("researcher");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [upgradeHint, setUpgradeHint] = useState(false);
  const [lastLink, setLastLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  function load() {
    setLoading(true);
    listMembers()
      .then((data) => {
        setMembers(data.members);
        setInvites(data.invites);
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!email.includes("@")) {
      setInviteError("Enter a valid email address.");
      return;
    }
    setInviting(true);
    setInviteError(null);
    setUpgradeHint(false);
    setCopied(false);
    try {
      const invite = await inviteMember(email.trim(), role);
      setEmail("");
      if (invite.accept_url) {
        const url = invite.accept_url.startsWith("http")
          ? invite.accept_url
          : `${window.location.origin}${invite.accept_url}`;
        setLastLink(url);
      }
      load();
    } catch (err) {
      if (err instanceof PlanLimitError) {
        setInviteError(err.message);
        setUpgradeHint(true);
      } else {
        setInviteError((err as Error).message);
      }
    } finally {
      setInviting(false);
    }
  }

  return (
    <main className="relative flex flex-1 overflow-hidden bg-surface">
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar searchPlaceholder="Search across workspace..." />
        <div className="flex-1 overflow-y-auto px-xl py-lg">
          <div className="mx-auto flex w-full max-w-[1100px] flex-col">
            <div className="mb-lg">
              <h2 className="mb-xs font-headline-lg text-headline-lg text-on-surface">
                Team Management
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Invite teammates and manage roles for your organization.
              </p>
            </div>

            {/* Invite form */}
            <form
              onSubmit={handleInvite}
              className="mb-lg rounded-xl border border-outline-variant/30 bg-surface-container-low p-lg"
            >
              <h3 className="mb-md font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                Invite a member
              </h3>
              <div className="flex flex-col gap-sm sm:flex-row">
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="teammate@company.com"
                  className="flex-1 rounded-lg border border-outline-variant/30 bg-surface-container px-md py-sm font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50"
                />
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="rounded-lg border border-outline-variant/30 bg-surface-container px-md py-sm font-body-md text-body-md capitalize text-on-surface focus:border-primary-fixed-dim/50 focus:outline-none"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
                <button
                  type="submit"
                  disabled={inviting}
                  className="flex items-center justify-center gap-sm rounded-lg bg-primary px-lg py-sm font-label-md text-label-md font-medium text-on-primary transition-all hover:bg-primary-fixed disabled:opacity-60"
                >
                  {inviting ? (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary/40 border-t-on-primary" />
                  ) : (
                    <MaterialIcon name="person_add" size={18} />
                  )}
                  Send invite
                </button>
              </div>
              {inviteError ? (
                <p className="mt-sm font-body-md text-[13px] text-error">
                  {inviteError}
                  {upgradeHint ? (
                    <a href="/billing" className="ml-sm underline hover:text-primary-fixed-dim">
                      Upgrade plan
                    </a>
                  ) : null}
                </p>
              ) : (
                <p className="mt-sm font-mono-sm text-[11px] text-on-surface-variant/70">
                  Invite emails are sent via the configured email adapter (console in local
                  mode).
                </p>
              )}

              {lastLink ? (
                <div className="mt-md rounded-lg border border-primary-fixed-dim/30 bg-primary-fixed/5 p-sm">
                  <div className="mb-xs flex items-center gap-xs font-mono-sm text-[11px] uppercase tracking-wider text-primary-fixed-dim">
                    <MaterialIcon name="link" size={14} />
                    Shareable invite link
                  </div>
                  <div className="flex items-center gap-sm">
                    <input
                      readOnly
                      value={lastLink}
                      onFocus={(e) => e.currentTarget.select()}
                      className="flex-1 truncate rounded border border-outline-variant/30 bg-surface-container px-sm py-xs font-mono-sm text-[12px] text-on-surface"
                    />
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(lastLink);
                          setCopied(true);
                          setTimeout(() => setCopied(false), 2000);
                        } catch {
                          setCopied(false);
                        }
                      }}
                      className="flex items-center gap-xs rounded border border-outline-variant/30 bg-surface-container px-sm py-xs font-label-md text-[12px] text-on-surface transition-colors hover:border-primary-fixed-dim/50"
                    >
                      <MaterialIcon name={copied ? "check" : "content_copy"} size={14} />
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </div>
                  <p className="mt-xs font-mono-sm text-[10px] text-on-surface-variant/60">
                    Share this link with the invitee. They can accept it to join your
                    organization.
                  </p>
                </div>
              ) : null}
            </form>

            {error ? (
              <div className="rounded-lg border border-error/20 bg-error-container/10 px-md py-sm font-body-md text-[13px] text-error">
                {error}
              </div>
            ) : loading ? (
              <div className="flex items-center justify-center py-xl">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
              </div>
            ) : (
              <>
                {/* Members table */}
                <div className="mb-lg overflow-hidden rounded-lg border border-outline-variant bg-surface-container-low">
                  <table className="w-full min-w-[600px] border-collapse text-left">
                    <thead>
                      <tr className="border-b border-outline-variant font-label-md text-label-md text-on-surface-variant">
                        <th className="px-md py-sm font-medium">Member</th>
                        <th className="px-md py-sm font-medium">Role</th>
                        <th className="px-md py-sm font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-outline-variant/50 font-body-md text-body-md text-on-surface">
                      {members.map((m) => (
                        <tr key={m.user_id}>
                          <td className="px-md py-sm">
                            <div className="flex items-center gap-sm">
                              <div className="flex h-7 w-7 items-center justify-center rounded-full border border-outline-variant/50 bg-surface-bright font-mono-sm text-[12px] text-on-surface">
                                {(m.email || m.user_id).charAt(0).toUpperCase()}
                              </div>
                              <div>
                                <div className="font-medium text-on-surface">{m.user_id}</div>
                                <div className="font-mono-sm text-[11px] text-on-surface-variant">
                                  {m.email}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-md py-sm">{roleBadge(m.role)}</td>
                          <td className="px-md py-sm">
                            <span className="inline-flex items-center gap-xs font-mono-sm text-[12px] text-primary-fixed-dim">
                              <span className="h-1.5 w-1.5 rounded-full bg-primary-fixed-dim" />
                              {m.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pending invites */}
                {invites.length > 0 ? (
                  <div>
                    <h3 className="mb-md font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                      Pending invites ({invites.length})
                    </h3>
                    <div className="flex flex-col gap-sm">
                      {invites.map((inv) => (
                        <div
                          key={inv.id}
                          className="flex items-center justify-between rounded-lg border border-outline-variant/30 bg-surface-container-low px-md py-sm"
                        >
                          <div className="flex items-center gap-sm">
                            <MaterialIcon name="mail" size={18} className="text-on-surface-variant" />
                            <span className="font-body-md text-body-md text-on-surface">
                              {inv.email}
                            </span>
                            {roleBadge(inv.role)}
                          </div>
                          <span className="font-mono-sm text-[11px] text-amber-400">
                            {inv.expired ? "Expired" : "Pending"}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
