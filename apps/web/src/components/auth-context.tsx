"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode
} from "react";
import {
  clearStoredRefreshToken,
  clearStoredToken,
  decodeTokenPayload,
  getStoredRefreshToken,
  getStoredToken,
  isTokenExpired,
  setStoredRefreshToken,
  setStoredToken
} from "@/lib/auth";
import { logout as apiLogout, refresh as apiRefresh } from "@/lib/auth-api";

type AuthState = {
  /** Active access token, or null when signed out. */
  token: string | null;
  /** True once the provider has resolved the persisted session (avoids SSR flash). */
  isReady: boolean;
  /** True when a non-expired access token is present. */
  isAuthenticated: boolean;
  /** Persist a new session. Pass the refresh token to enable silent renewal. */
  signIn: (token: string, refreshToken?: string) => void;
  signOut: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

// Renew this many ms before the access token actually expires.
const RENEW_SKEW_MS = 60_000;

function msUntilRenew(token: string): number {
  const exp = decodeTokenPayload(token)?.exp;
  if (typeof exp !== "number") return Number.POSITIVE_INFINITY;
  return exp * 1000 - Date.now() - RENEW_SKEW_MS;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [isReady, setIsReady] = useState(false);
  const renewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearLocal = useCallback(() => {
    clearStoredToken();
    clearStoredRefreshToken();
    setToken(null);
  }, []);

  // Exchange the stored refresh token for a fresh access token.
  const doRefresh = useCallback(async (): Promise<boolean> => {
    const rt = getStoredRefreshToken();
    if (!rt) {
      clearLocal();
      return false;
    }
    try {
      const result = await apiRefresh(rt);
      setStoredToken(result.token);
      if (result.refresh_token) setStoredRefreshToken(result.refresh_token);
      setToken(result.token);
      return true;
    } catch {
      // Refresh failed (expired/revoked/reused) — force a clean sign-out.
      clearLocal();
      return false;
    }
  }, [clearLocal]);

  // Resolve the persisted session on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = getStoredToken();
      if (stored && !isTokenExpired(stored)) {
        if (!cancelled) setToken(stored);
      } else if (getStoredRefreshToken()) {
        // Access token missing/expired but we have a refresh token — renew.
        await doRefresh();
      } else if (stored) {
        clearStoredToken();
      }
      if (!cancelled) setIsReady(true);
    })();

    function onStorage(event: StorageEvent) {
      if (event.key === "omni_token") setToken(event.newValue);
    }
    window.addEventListener("storage", onStorage);
    return () => {
      cancelled = true;
      window.removeEventListener("storage", onStorage);
    };
  }, [doRefresh]);

  // Schedule silent renewal whenever the access token changes.
  useEffect(() => {
    if (renewTimer.current) {
      clearTimeout(renewTimer.current);
      renewTimer.current = null;
    }
    if (!token) return;
    const delay = msUntilRenew(token);
    if (!Number.isFinite(delay)) return; // no exp claim — nothing to schedule
    renewTimer.current = setTimeout(() => void doRefresh(), Math.max(1_000, delay));
    return () => {
      if (renewTimer.current) clearTimeout(renewTimer.current);
    };
  }, [token, doRefresh]);

  const signIn = useCallback((next: string, refreshToken?: string) => {
    setStoredToken(next);
    if (refreshToken) setStoredRefreshToken(refreshToken);
    setToken(next);
  }, []);

  const signOut = useCallback(() => {
    const rt = getStoredRefreshToken();
    void apiLogout(rt ?? ""); // best-effort server-side revocation
    clearLocal();
  }, [clearLocal]);

  const value = useMemo<AuthState>(
    () => ({
      token,
      isReady,
      isAuthenticated: Boolean(token) && !isTokenExpired(token as string),
      signIn,
      signOut
    }),
    [token, isReady, signIn, signOut]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
