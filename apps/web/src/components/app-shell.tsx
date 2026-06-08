"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { MaterialIcon } from "@/components/material-icon";

type NavItem = { href: string; label: string; icon: string };

const primaryNav: NavItem[] = [
  { href: "/", label: "Dashboard", icon: "dashboard" },
  { href: "/upload", label: "Upload", icon: "cloud_upload" },
  { href: "/research", label: "Research", icon: "science" },
  { href: "/documents", label: "Documents", icon: "description" },
  { href: "/projects", label: "Projects", icon: "folder_open" },
  { href: "/archives", label: "Archives", icon: "history" },
  { href: "/settings", label: "Settings", icon: "settings" }
];

const footerNav: NavItem[] = [
  { href: "/settings", label: "Account", icon: "account_circle" },
  { href: "/", label: "Log out", icon: "logout" }
];

function isActiveHref(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex min-h-screen bg-surface text-on-surface">
      {/* SideNavBar — desktop */}
      <nav className="fixed left-0 top-0 z-40 hidden h-screen w-sidebar_width flex-col border-r border-outline-variant/20 bg-surface-container-low py-xl shadow-xl md:flex">
        <div className="mb-xl flex items-center gap-md px-lg">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-fixed-dim text-on-primary-fixed shadow-[0_0_15px_rgba(0,218,243,0.3)]">
            <MaterialIcon name="science" size={18} fill />
          </span>
          <div>
            <h1 className="font-headline-md text-headline-md font-black tracking-tight text-on-surface">
              OMERO
            </h1>
            <p className="font-mono-sm text-mono-sm text-primary-fixed-dim/80">AI Research</p>
          </div>
        </div>

        <div className="mb-lg px-lg">
          <Link
            href="/research"
            className="flex w-full items-center justify-center gap-sm rounded-lg border border-outline-variant/30 bg-surface-container-high px-md py-md font-label-md text-label-md text-primary transition-all duration-200 hover:border-primary-fixed-dim/50"
          >
            <MaterialIcon name="add" size={18} />
            New Analysis
          </Link>
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
                      ? "flex items-center gap-md rounded-r-md border-l-2 border-primary-fixed-dim bg-surface-container-high/50 px-md py-sm font-label-md text-label-md text-primary-fixed-dim transition-all duration-200"
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
            {footerNav.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="flex items-center gap-md rounded-md px-md py-sm font-label-md text-label-md text-on-surface-variant transition-all duration-200 hover:bg-surface-container-high hover:text-on-surface"
                >
                  <MaterialIcon name={item.icon} size={18} />
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </nav>

      {/* Main column */}
      <div className="flex min-h-screen flex-1 flex-col md:ml-[240px]">
        {/* Mobile top bar */}
        <header className="sticky top-0 z-50 flex h-16 w-full items-center justify-between border-b border-outline-variant/30 bg-surface/80 px-lg shadow-sm backdrop-blur-xl md:hidden">
          <h1 className="font-headline-md text-headline-md font-bold tracking-tight text-on-surface">
            OMERO
          </h1>
          <button
            type="button"
            onClick={() => setMobileOpen((open) => !open)}
            className="rounded-full p-sm text-on-surface-variant transition-colors hover:bg-surface-container-highest/50 active:scale-95"
            aria-label="Toggle navigation"
          >
            <MaterialIcon name={mobileOpen ? "close" : "menu"} />
          </button>
        </header>

        {/* Mobile nav drawer */}
        {mobileOpen ? (
          <nav className="border-b border-outline-variant/20 bg-surface-container-low px-sm py-sm md:hidden">
            <ul className="flex flex-col gap-xs">
              {primaryNav.map((item) => {
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
    </div>
  );
}
