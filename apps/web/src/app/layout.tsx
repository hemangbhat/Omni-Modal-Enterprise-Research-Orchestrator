import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppShell } from "@/components/app-shell";
import { AuthProvider } from "@/components/auth-context";
import { SentryErrorBoundary } from "@/components/sentry-error-boundary";
import { initSentry } from "@/lib/sentry";
import "./globals.css";

// Initialize Sentry at module load (Requirement 1.1)
initSentry();

export const metadata: Metadata = {
  title: "OMERO: research from your own documents",
  description: "Ask questions of your documents and recordings, and get answers that cite the page they came from.",
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
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap"
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block"
        />
      </head>
      <body>
        <SentryErrorBoundary>
          <AuthProvider>
            <AppShell>{children}</AppShell>
          </AuthProvider>
        </SentryErrorBoundary>
      </body>
    </html>
  );
}
