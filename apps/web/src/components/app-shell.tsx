"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { AssistantPanel } from "@/components/assistant-panel";
import { MaterialIcon } from "@/components/material-icon";
import { Wordmark } from "@/components/wordmark";
import { useAuth } from "@/components/auth-context";

type NavItem = { href: string; label: string; icon: string };

const primaryNav: NavItem[] = [
  { href: "/", label: "Dashboard", icon: "dashboard" },
  { href: "/upload", label: "Upload", icon: "cloud_upload" },
  { href: "/research", label: "Research", icon: "science" },
  { href: "/documents", label: "Documents", icon: "description" },
  { href: "/projects", label: "Projects", icon: "folder_open" },
  { href: "/archives", label: "Archives", icon: "history" }
];

const manageNav: NavItem[] = [
  { href: "/usage", label: "Usage", icon: "monitoring" },
  { href: "/team", label: "Team", icon: "group" },
  { href: "/billing", label: "Billing", icon: "credit_card" },
  { href: "/admin", label: "Admin", icon: "admin_panel_settings" },
  { href: "/settings", label: "Settings", icon: "settings" }
];

const allNav: NavItem[] = [...primaryNav, ...manageNav];

function isActiveHref(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

const PUBLIC_ROUTES = new Set([
  "/sign-in",
  "/sign-up",
  "/accept-invite",
  "/forgot-password",
  "/reset-password",
  "/pricing"
]);

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { isReady, isAuthenticated, signOut } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const closeAssistant = useCallback(() => setAssistantOpen(false), []);

  const isPublicRoute = PUBLIC_ROUTES.has(pathname);

  // Auth guard: send unauthenticated users to the sign-in page.
  useEffect(() => {
    if (isReady && !isAuthenticated && !isPublicRoute) {
      router.replace("/sign-in");
    }
  }, [isReady, isAuthenticated, isPublicRoute, router]);

  // Ctrl+J / Cmd+J toggles the assistant on any signed-in page.
  useEffect(() => {
    if (isPublicRoute || !isAuthenticated) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setAssistantOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isPublicRoute, isAuthenticated]);

  function handleSignOut() {
    signOut();
    router.replace("/sign-in");
  }

  // Public routes (sign-in) render without the workspace chrome.
  if (isPublicRoute) {
    return <>{children}</>;
  }

  // While resolving the session, or while redirecting an unauthenticated
  // user, show a lightweight loader instead of flashing protected content.
  if (!isReady || !isAuthenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface text-on-surface-variant">
        <div className="flex items-center gap-md">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-outline-variant border-t-primary-fixed-dim" />
          <span className="font-mono-sm text-mono-sm">
            {isReady ? "Redirecting to sign-in…" : "Restoring session…"}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-surface text-on-surface">
      {/* SideNavBar — desktop */}
      <nav className="fixed left-0 top-0 z-40 hidden h-screen w-sidebar_width flex-col border-r border-outline-variant/40 bg-surface-container-lowest py-xl md:flex">
        <Link href="/" aria-label="OMERO home" className="mb-xl block rounded-md px-lg">
          <Wordmark />
        </Link>

        <div className="mb-lg px-lg">
          <Link
            href="/research"
            className="flex w-full items-center justify-center gap-sm rounded-lg bg-primary-fixed-dim px-md py-[10px] font-label-md text-label-md font-semibold text-on-primary-fixed transition-colors duration-150 hover:bg-primary"
          >
            <MaterialIcon name="add" size={18} />
            New question
          </Link>
          <button
            type="button"
            onClick={() => setAssistantOpen((open) => !open)}
            aria-expanded={assistantOpen}
            className="mt-sm flex w-full items-center justify-between gap-sm rounded-lg px-md py-sm font-label-md text-label-md text-on-surface-variant transition-colors duration-200 hover:bg-surface-container-high hover:text-on-surface"
          >
            <span className="flex items-center gap-md">
              <MaterialIcon name="forum" size={18} />
              Assistant
            </span>
            <kbd className="rounded border border-outline-variant/40 px-[5px] font-mono-sm text-[10px] text-on-surface-variant/70">
              Ctrl J
            </kbd>
          </button>
        </div>

        <ul className="flex flex-1 flex-col gap-xs px-sm">
          {primaryNav.map((item) => {
            const active = isActiveHref(pathname, item.href);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={
                    active
                      ? "flex items-center gap-md rounded-r-md border-l-2 border-primary-fixed-dim bg-surface-container-high px-md py-sm font-label-md text-label-md font-semibold text-on-surface transition-colors duration-150"
                      : "flex items-center gap-md rounded-md px-md py-sm font-label-md text-label-md text-on-surface-variant transition-all duration-200 hover:bg-surface-container-high hover:text-on-surface"
                  }
                >
                  <MaterialIcon name={item.icon} size={18} fill={active} />
                  {item.label}
                </Link>
              </li>
            );
          })}

          <li className="my-sm px-md">
            <span className="font-mono-sm text-[11px] text-on-surface-variant">
              Manage
            </span>
          </li>

          {manageNav.map((item) => {
            const active = isActiveHref(pathname, item.href);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={
                    active
                      ? "flex items-center gap-md rounded-r-md border-l-2 border-primary-fixed-dim bg-surface-container-high px-md py-sm font-label-md text-label-md font-semibold text-on-surface transition-colors duration-150"
                      : "flex items-center gap-md rounded-md px-md py-sm font-label-md text-label-md text-on-surface-variant transition-all duration-200 hover:bg-surface-container-high hover:text-on-surface"
                  }
                >
                  <MaterialIcon name={item.icon} size={18} fill={active} />
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>

        <div className="mt-auto px-sm">
          <ul className="flex flex-col gap-xs">
            <li>
              <Link
                href="/settings"
                className="flex items-center gap-md rounded-md px-md py-sm font-label-md text-label-md text-on-surface-variant transition-all duration-200 hover:bg-surface-container-high hover:text-on-surface"
              >
                <MaterialIcon name="account_circle" size={18} />
                Account
              </Link>
            </li>
            <li>
              <button
                type="button"
                onClick={handleSignOut}
                className="flex w-full items-center gap-md rounded-md px-md py-sm font-label-md text-label-md text-on-surface-variant transition-all duration-200 hover:bg-error-container/20 hover:text-error"
              >
                <MaterialIcon name="logout" size={18} />
                Log out
              </button>
            </li>
          </ul>
        </div>
      </nav>

      {/* Main column */}
      <div className="flex min-h-screen min-w-0 flex-1 flex-col overflow-x-clip md:ml-[240px]">
        {/* Mobile top bar */}
        <header className="sticky top-0 z-50 flex h-16 w-full items-center justify-between border-b border-outline-variant/30 bg-surface/80 px-lg shadow-sm backdrop-blur-xl md:hidden">
          <Link href="/" aria-label="OMERO home" className="rounded-md">
            <Wordmark tagline={false} />
          </Link>
          <div className="flex items-center gap-xs">
            <button
              type="button"
              onClick={() => setAssistantOpen((open) => !open)}
              className="rounded-full p-sm text-on-surface-variant transition-colors hover:bg-surface-container-highest/50 active:scale-95"
              aria-label="Open assistant"
            >
              <MaterialIcon name="forum" />
            </button>
            <button
              type="button"
              onClick={() => setMobileOpen((open) => !open)}
              className="rounded-full p-sm text-on-surface-variant transition-colors hover:bg-surface-container-highest/50 active:scale-95"
              aria-label="Toggle navigation"
            >
              <MaterialIcon name={mobileOpen ? "close" : "menu"} />
            </button>
          </div>
        </header>

        {/* Mobile nav drawer */}
        {mobileOpen ? (
          <nav className="border-b border-outline-variant/20 bg-surface-container-low px-sm py-sm md:hidden">
            <ul className="flex flex-col gap-xs">
              {allNav.map((item) => {
                const active = isActiveHref(pathname, item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={() => setMobileOpen(false)}
                      className={
                        active
                          ? "flex items-center gap-md rounded-md bg-surface-container-high/50 px-md py-sm font-label-md text-label-md text-primary-fixed-dim"
                          : "flex items-center gap-md rounded-md px-md py-sm font-label-md text-label-md text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
                      }
                    >
                      <MaterialIcon name={item.icon} size={18} fill={active} />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        ) : null}

        {children}
      </div>
      <AssistantPanel open={assistantOpen} onClose={closeAssistant} />
    </div>
  );
}
