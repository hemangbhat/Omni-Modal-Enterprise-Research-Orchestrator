"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from "react";
import {
  clearStoredToken,
  getStoredToken,
  isTokenExpired,
  setStoredToken
} from "@/lib/auth";

type AuthState = {
  /** Active session token, or null when signed out. */
  token: string | null;
  /** True once the provider has read the persisted token (avoids SSR flash). */
  isReady: boolean;
  /** True when a non-expired token is present. */
  isAuthenticated: boolean;
  signIn: (token: string) => void;
  signOut: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const stored = getStoredToken();
    if (stored && isTokenExpired(stored)) {
      // Expired session — clear it so the user is sent back to sign-in.
      clearStoredToken();
      setToken(null);
    } else {
      setToken(stored);
    }
    setIsReady(true);

    // Keep multiple tabs in sync.
    function onStorage(event: StorageEvent) {
      if (event.key === "omni_token") {
        setToken(event.newValue);
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const signIn = useCallback((next: string) => {
    setStoredToken(next);
    setToken(next);
  }, []);

  const signOut = useCallback(() => {
    clearStoredToken();
    setToken(null);
  }, []);

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
