/**
 * Client for the backend credential-auth endpoints (Phase E).
 *
 * `POST /auth/register` and `POST /auth/login` verify email+password against
 * PBKDF2-hashed credentials and return a signed backend JWT, which the existing
 * auth context stores and sends on every API call. These endpoints are
 * unauthenticated (they mint the token), so no bearer header is attached here.
 */

import { apiRequest } from "@/lib/api-client";
import { getClientApiConfig } from "@/lib/env";

export type AuthResult = {
  token: string;
  tenant_id: string;
  user_id: string;
  roles: string[];
  email: string;
  expires_at: number;
  access_expires_at?: number;
  refresh_token?: string;
  refresh_expires_at?: number;
};

async function post(path: string, body: unknown): Promise<AuthResult> {
  const { baseUrl } = getClientApiConfig();
  const res = await apiRequest(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    },
    { baseUrl }
  );
  const data = (await res.json().catch(() => ({}))) as Partial<AuthResult> & { error?: string };
  if (!res.ok) {
    throw new Error(data.error ?? `Authentication failed (HTTP ${res.status}).`);
  }
  return data as AuthResult;
}

export function login(email: string, password: string): Promise<AuthResult> {
  return post("/auth/login", { email, password });
}

export function register(
  email: string,
  password: string,
  displayName?: string
): Promise<AuthResult> {
  return post("/auth/register", {
    email,
    password,
    display_name: displayName
  });
}

/**
 * Exchange a refresh token for a fresh access token (and a rotated refresh
 * token). Throws if the refresh token is invalid, expired, or revoked.
 */
export function refresh(refreshToken: string): Promise<AuthResult> {
  return post("/auth/refresh", { refresh_token: refreshToken });
}

/**
 * Revoke a refresh token server-side (sign-out). Best-effort: never throws, so
 * the UI can always complete a local sign-out even if the network call fails.
 */
export async function logout(refreshToken: string): Promise<void> {
  if (!refreshToken) return;
  try {
    const { baseUrl } = getClientApiConfig();
    await apiRequest(
      "/auth/logout",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken })
      },
      { baseUrl }
    );
  } catch {
    /* best-effort — local sign-out proceeds regardless */
  }
}
