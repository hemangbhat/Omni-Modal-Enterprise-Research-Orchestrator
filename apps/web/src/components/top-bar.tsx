"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { MaterialIcon } from "@/components/material-icon";
import { useAuth } from "@/components/auth-context";
import { apiRequest } from "@/lib/api-client";
import { decodeTokenPayload } from "@/lib/auth";
import { getClientApiConfig } from "@/lib/env";
import {
  listNotifications,
  listWorkspaces,
  markAllNotificationsRead,
  type Notification,
  type Workspace
} from "@/lib/saas-api";
import { ACTIVE_WORKSPACE_KEY, setActiveWorkspaceId } from "@/lib/workspace";

type TopBarProps = {
  searchPlaceholder?: string;
};

type BackendStatus = "checking" | "online" | "offline";

function timeAgo(epochSeconds: number): string {
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const NOTE_ICON: Record<string, string> = {
  info: "info",
  success: "check_circle",
  warning: "warning",
  error: "error"
};

/**
 * Desktop top app bar used across content pages: a working workspace search,
 * a live backend status indicator, a notifications popover, and a profile
 * menu with sign-out.
 */
export function TopBar({ searchPlaceholder = "Search workspace..." }: TopBarProps) {
  const router = useRouter();
  const { token, signOut } = useAuth();

  const [search, setSearch] = useState("");
  const [openMenu, setOpenMenu] = useState<
    null | "notifications" | "status" | "profile" | "workspace"
  >(null);
  const [status, setStatus] = useState<BackendStatus>("checking");
  const [notes, setNotes] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Load notification unread count + workspaces once a token is present.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listNotifications()
      .then((data) => {
        if (cancelled) return;
        setNotes(data.notifications);
        setUnread(data.unread);
      })
      .catch(() => undefined);
    listWorkspaces()
      .then((data) => {
        if (cancelled) return;
        setWorkspaces(data.workspaces);
        const stored =
          typeof window !== "undefined"
            ? window.localStorage.getItem(ACTIVE_WORKSPACE_KEY)
            : null;
        const match = data.workspaces.find((w) => w.id === stored);
        setActiveWorkspace(match?.id ?? data.workspaces[0]?.id ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Refresh notifications when the popover opens.
  useEffect(() => {
    if (openMenu !== "notifications" || !token) return;
    listNotifications()
      .then((data) => {
        setNotes(data.notifications);
        setUnread(data.unread);
      })
      .catch(() => undefined);
  }, [openMenu, token]);

  function selectWorkspace(id: string) {
    setActiveWorkspace(id);
    setActiveWorkspaceId(id);
    setOpenMenu(null);
    // Re-fetch workspace-scoped pages with the new active workspace.
    router.refresh();
  }

  async function handleMarkAllRead() {
    try {
      await markAllNotificationsRead();
      setNotes((prev) => prev.map((n) => ({ ...n, read: true })));
      setUnread(0);
    } catch {
      // best-effort
    }
  }

  // Close any open popover when clicking outside the top bar controls.
  useEffect(() => {
    function onClick(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpenMenu(null);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Ping backend /health when the status popover opens (real check).
  useEffect(() => {
    if (openMenu !== "status") return;
    let cancelled = false;
    setStatus("checking");
    const { baseUrl } = getClientApiConfig();
    apiRequest("/health", { method: "GET" }, { baseUrl, timeout: 5_000 })
      .then((r) => {
        if (!cancelled) setStatus(r.ok ? "online" : "offline");
      })
      .catch(() => {
        if (!cancelled) setStatus("offline");
      });
    return () => {
      cancelled = true;
    };
  }, [openMenu]);

  function submitSearch() {
    const term = search.trim();
    if (!term) return;
    setOpenMenu(null);
    router.push(`/documents?q=${encodeURIComponent(term)}`);
  }

  function handleSignOut() {
    setOpenMenu(null);
    signOut();
    router.replace("/sign-in");
  }

  const claims = token ? decodeTokenPayload(token) : null;
  const userId = typeof claims?.user_id === "string" ? claims.user_id : "user";
  const tenantId = typeof claims?.tenant_id === "string" ? claims.tenant_id : "—";
  const roles = Array.isArray(claims?.roles) ? (claims?.roles as string[]) : [];
  const avatarLetter = userId.charAt(0).toUpperCase() || "U";

  return (
    <header
      ref={containerRef}
      className="sticky top-0 z-40 flex h-16 w-full items-center justify-between border-b border-outline-variant/20 bg-surface/80 px-lg backdrop-blur-md md:px-xl"
    >
      <div className="relative w-full max-w-md">
        <MaterialIcon
          name="search"
          size={18}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
        />
        <input
          type="text"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              submitSearch();
            }
          }}
          placeholder={searchPlaceholder}
          aria-label="Search workspace"
          className="w-full rounded-full border border-outline-variant/30 bg-surface-container py-1.5 pl-10 pr-4 font-body-md text-body-md text-on-surface transition-all placeholder:text-on-surface-variant/50 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50"
        />
      </div>

      <div className="flex items-center gap-md">
        {/* Workspace switcher */}
        {workspaces.length > 0 ? (
          <div className="relative">
            <button
              type="button"
              onClick={() => setOpenMenu((m) => (m === "workspace" ? null : "workspace"))}
              className={`flex items-center gap-sm rounded-lg border px-md py-1.5 transition-all ${
                openMenu === "workspace"
                  ? "border-primary-fixed-dim/50 bg-surface-bright"
                  : "border-outline-variant/30 bg-surface-container hover:border-outline-variant/60"
              }`}
              aria-label="Switch workspace"
              aria-expanded={openMenu === "workspace"}
            >
              <MaterialIcon name="workspaces" size={16} className="text-primary-fixed-dim" />
              <span className="max-w-[140px] truncate font-label-md text-label-md text-on-surface">
                {workspaces.find((w) => w.id === activeWorkspace)?.name ?? "Workspace"}
              </span>
              <MaterialIcon name="expand_more" size={16} className="text-on-surface-variant" />
            </button>
            {openMenu === "workspace" ? (
              <div className="absolute left-0 top-12 z-50 w-64 overflow-hidden rounded-2xl border border-outline-variant/20 bg-surface-container-low shadow-[0_12px_40px_rgba(0,0,0,0.4)] backdrop-blur-xl">
                <PopoverHeader icon="workspaces" title="Workspaces" />
                <div className="flex flex-col py-xs">
                  {workspaces.map((w) => (
                    <button
                      key={w.id}
                      type="button"
                      onClick={() => selectWorkspace(w.id)}
                      className="flex items-center justify-between px-lg py-sm font-body-md text-[14px] text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-on-surface"
                    >
                      <span className="truncate">{w.name}</span>
                      {w.id === activeWorkspace ? (
                        <MaterialIcon name="check" size={16} className="text-primary-fixed-dim" />
                      ) : null}
                    </button>
                  ))}
                </div>
                <Link
                  href="/team"
                  onClick={() => setOpenMenu(null)}
                  className="flex items-center gap-sm border-t border-outline-variant/10 px-lg py-sm font-label-md text-label-md text-primary-fixed-dim transition-colors hover:bg-surface-container-high"
                >
                  <MaterialIcon name="settings" size={16} />
                  Manage workspaces
                </Link>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Notifications */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setOpenMenu((m) => (m === "notifications" ? null : "notifications"))}
            className={`relative flex h-9 w-9 items-center justify-center rounded-full transition-all hover:bg-surface-bright hover:text-primary-fixed-dim ${
              openMenu === "notifications" ? "bg-surface-bright text-primary-fixed-dim" : "text-on-surface-variant"
            }`}
            aria-label="Notifications"
            aria-expanded={openMenu === "notifications"}
          >
            <MaterialIcon name="notifications" />
            {unread > 0 ? (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-error px-1 font-mono-sm text-[9px] font-bold text-white">
                {unread > 9 ? "9+" : unread}
              </span>
            ) : null}
          </button>
          {openMenu === "notifications" ? (
            <Popover>
              <div className="flex items-center justify-between border-b border-outline-variant/10 px-lg py-md">
                <div className="flex items-center gap-sm">
                  <MaterialIcon name="notifications" size={16} className="text-primary-fixed-dim" />
                  <span className="font-label-md text-label-md text-on-surface">Notifications</span>
                </div>
                {unread > 0 ? (
                  <button
                    type="button"
                    onClick={handleMarkAllRead}
                    className="font-mono-sm text-[11px] text-primary-fixed-dim hover:underline"
                  >
                    Mark all read
                  </button>
                ) : null}
              </div>
              {notes.length === 0 ? (
                <div className="flex flex-col items-center gap-sm px-lg py-xl text-center">
                  <MaterialIcon name="check_circle" size={28} className="text-on-surface-variant/40" />
                  <p className="font-body-md text-[14px] text-on-surface-variant">
                    You&apos;re all caught up. No new notifications.
                  </p>
                </div>
              ) : (
                <ul className="max-h-80 overflow-y-auto py-xs">
                  {notes.slice(0, 12).map((n) => (
                    <li
                      key={n.id}
                      className={`flex gap-sm px-lg py-sm ${n.read ? "opacity-60" : ""}`}
                    >
                      <MaterialIcon
                        name={NOTE_ICON[n.kind] ?? "info"}
                        size={16}
                        className={
                          n.kind === "error"
                            ? "mt-0.5 text-error"
                            : n.kind === "warning"
                              ? "mt-0.5 text-amber-400"
                              : "mt-0.5 text-primary-fixed-dim"
                        }
                      />
                      <div className="min-w-0 flex-1">
                        <p className="font-body-md text-[13px] font-medium text-on-surface">
                          {n.title}
                        </p>
                        {n.body ? (
                          <p className="font-body-md text-[12px] text-on-surface-variant">
                            {n.body}
                          </p>
                        ) : null}
                        <p className="mt-0.5 font-mono-sm text-[10px] text-on-surface-variant/60">
                          {timeAgo(n.created_at)}
                        </p>
                      </div>
                      {!n.read ? (
                        <span className="mt-1 h-2 w-2 flex-shrink-0 rounded-full bg-primary-fixed-dim" />
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </Popover>
          ) : null}
        </div>

        {/* Live backend status */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setOpenMenu((m) => (m === "status" ? null : "status"))}
            className={`flex h-9 w-9 items-center justify-center rounded-full transition-all hover:bg-surface-bright hover:text-primary-fixed-dim ${
              openMenu === "status" ? "bg-surface-bright text-primary-fixed-dim" : "text-on-surface-variant"
            }`}
            aria-label="Backend status"
            aria-expanded={openMenu === "status"}
          >
            <MaterialIcon name="sensors" />
          </button>
          {openMenu === "status" ? (
            <Popover>
              <PopoverHeader icon="sensors" title="System status" />
              <div className="flex items-center justify-between px-lg py-md">
                <span className="font-body-md text-[14px] text-on-surface-variant">Backend API</span>
                <span className="flex items-center gap-sm font-mono-sm text-[12px]">
                  {status === "checking" ? (
                    <>
                      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-400" />
                      <span className="text-amber-400">Checking…</span>
                    </>
                  ) : status === "online" ? (
                    <>
                      <span className="h-2 w-2 rounded-full bg-primary-fixed-dim" />
                      <span className="text-primary-fixed-dim">Online</span>
                    </>
                  ) : (
                    <>
                      <span className="h-2 w-2 rounded-full bg-error" />
                      <span className="text-error">Offline</span>
                    </>
                  )}
                </span>
              </div>
              {status === "offline" ? (
                <p className="border-t border-outline-variant/10 px-lg py-md font-mono-sm text-[11px] text-on-surface-variant/70">
                  Could not reach the API. Ensure the backend is running.
                </p>
              ) : null}
            </Popover>
          ) : null}
        </div>

        {/* Profile menu */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setOpenMenu((m) => (m === "profile" ? null : "profile"))}
            className="ml-sm flex h-8 w-8 items-center justify-center overflow-hidden rounded-full border border-outline-variant/50 bg-gradient-to-tr from-surface-variant to-outline transition-all hover:ring-2 hover:ring-primary-fixed-dim/40"
            aria-label="Account menu"
            aria-expanded={openMenu === "profile"}
          >
            <span className="font-mono-sm text-mono-sm text-on-surface">{avatarLetter}</span>
          </button>
          {openMenu === "profile" ? (
            <Popover>
              <div className="border-b border-outline-variant/10 px-lg py-md">
                <p className="font-body-md text-[14px] font-medium text-on-surface">{userId}</p>
                <p className="font-mono-sm text-[11px] text-on-surface-variant">tenant: {tenantId}</p>
                {roles.length > 0 ? (
                  <div className="mt-sm flex flex-wrap gap-xs">
                    {roles.map((role) => (
                      <span
                        key={role}
                        className="rounded border border-outline-variant/20 bg-surface-container px-sm py-0.5 font-mono-sm text-[10px] uppercase tracking-wider text-primary-fixed-dim"
                      >
                        {role}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="flex flex-col py-xs">
                <Link
                  href="/settings"
                  onClick={() => setOpenMenu(null)}
                  className="flex items-center gap-md px-lg py-sm font-label-md text-label-md text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-on-surface"
                >
                  <MaterialIcon name="account_circle" size={18} />
                  Account settings
                </Link>
                <button
                  type="button"
                  onClick={handleSignOut}
                  className="flex items-center gap-md px-lg py-sm font-label-md text-label-md text-on-surface-variant transition-colors hover:bg-error-container/20 hover:text-error"
                >
                  <MaterialIcon name="logout" size={18} />
                  Sign out
                </button>
              </div>
            </Popover>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function Popover({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute right-0 top-12 z-50 w-72 overflow-hidden rounded-2xl border border-outline-variant/20 bg-surface-container-low shadow-[0_12px_40px_rgba(0,0,0,0.4)] backdrop-blur-xl">
      {children}
    </div>
  );
}

function PopoverHeader({ icon, title }: { icon: string; title: string }) {
  return (
    <div className="flex items-center gap-sm border-b border-outline-variant/10 px-lg py-md">
      <MaterialIcon name={icon} size={16} className="text-primary-fixed-dim" />
      <span className="font-label-md text-label-md text-on-surface">{title}</span>
    </div>
  );
}
