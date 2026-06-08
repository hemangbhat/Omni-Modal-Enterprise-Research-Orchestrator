import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppShell } from "@/components/app-shell";
import { SentryErrorBoundary } from "@/components/sentry-error-boundary";
import { initSentry } from "@/lib/sentry";
import "./globals.css";

// Initialize Sentry at module load (Requirement 1.1)
initSentry();

export const metadata: Metadata = {
  title: "Omni-Modal Research Orchestrator",
  description: "Enterprise research orchestration workspace",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;900&family=JetBrains+Mono:wght@400;500&display=swap"
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block"
        />
      </head>
      <body>
        <SentryErrorBoundary>
          <AppShell>{children}</AppShell>
        </SentryErrorBoundary>
      </body>
    </html>
  );
}
