/**
 * Client-side session token storage.
 *
 * This project authenticates the browser with a JWT bearer token verified by
 * the backend (HS256). For local development and portfolio demos the token is
 * stored in localStorage so that sign-in / sign-out actually gate access and
 * persist across reloads. In a production deployment the token would be issued
 * by an identity provider and stored in an httpOnly cookie instead.
 */

const TOKEN_KEY = "omni_token";

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* localStorage unavailable (private mode, SSR) — ignore */
  }
}

export function clearStoredToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * The build-time demo/dev token, if one was configured via
 * NEXT_PUBLIC_API_TOKEN. Used to power the one-click "Continue with demo
 * token" button on the sign-in page. Returns null when not configured.
 */
export function getDemoToken(): string | null {
  return process.env.NEXT_PUBLIC_API_TOKEN ?? null;
}

/**
 * Best-effort decode of a JWT payload (no signature verification — display
 * only). Returns null if the token is malformed.
 */
export function decodeTokenPayload(
  token: string
): Record<string, unknown> | null {
  try {
    const [, payload] = token.split(".");
    if (!payload) return null;
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const json =
      typeof atob === "function"
        ? atob(normalized)
        : Buffer.from(normalized, "base64").toString("utf-8");
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * True when the token is a JWT whose `exp` claim is in the past. Tokens that
 * cannot be decoded are treated as non-expired (the backend remains the
 * source of truth for validity).
 */
export function isTokenExpired(token: string): boolean {
  const payload = decodeTokenPayload(token);
  const exp = payload?.exp;
  if (typeof exp !== "number") return false;
  return exp * 1000 < Date.now();
}
