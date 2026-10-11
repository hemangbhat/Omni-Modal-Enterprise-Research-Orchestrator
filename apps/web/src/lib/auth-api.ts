/**
 * Client for the backend credential-auth endpoints.
 *
 * `POST /auth/register` and `POST /auth/login` verify email+password against
 * PBKDF2-hashed credentials and return a signed backend JWT, which the auth
 * context stores and sends on every API call. When the account has two-step
 * sign-in (TOTP MFA) enabled, login returns a challenge instead and
 * `mfaLogin` completes the session. The MFA management calls (status, enroll,
 * confirm, disable) act on the signed-in user and send the bearer token.
 */

import { parseApiError } from "@/lib/api-error";
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

/** Returned by login when the account requires a second factor. */
export type MfaChallenge = { mfa_required: true; mfa_token: string };

export function isMfaChallenge(result: AuthResult | MfaChallenge): result is MfaChallenge {
  return (result as MfaChallenge).mfa_required === true;
}

/**
 * A rejected MFA login. `nextToken` is the challenge token to retry with, or
 * null when the challenge is over (expired, or too many wrong codes) and the
 * user must enter their password again.
 */
export class MfaCodeError extends Error {
  nextToken: string | null;
  constructor(message: string, nextToken: string | null) {
    super(message);
    this.name = "MfaCodeError";
    this.nextToken = nextToken;
  }
}

type CallOptions = { method?: "GET" | "POST"; authenticated?: boolean };

async function send(path: string, body: unknown, options: CallOptions = {}): Promise<Response> {
  const { baseUrl, token } = getClientApiConfig();
  const method = options.method ?? "POST";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options.authenticated && token) headers.Authorization = `Bearer ${token}`;
  return apiRequest(
    path,
    { method, headers, ...(method === "POST" ? { body: JSON.stringify(body) } : {}) },
    { baseUrl }
  );
}

async function call<T>(path: string, body: unknown, options: CallOptions = {}): Promise<T> {
  const res = await send(path, body, options);
  const data: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(parseApiError(data).message ?? `Request failed (HTTP ${res.status}).`);
  }
  return data as T;
}

export function login(email: string, password: string): Promise<AuthResult | MfaChallenge> {
  return call("/auth/login", { email, password });
}

export function register(
  email: string,
  password: string,
  displayName?: string
): Promise<AuthResult> {
  return call("/auth/register", {
    email,
    password,
    display_name: displayName
  });
}

/** Complete a sign-in that returned an MFA challenge. Throws MfaCodeError. */
export async function mfaLogin(mfaToken: string, code: string): Promise<AuthResult> {
  const res = await send("/auth/mfa/login", { mfa_token: mfaToken, code });
  const data: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    const { message, fields } = parseApiError(data);
    const nextToken = typeof fields.mfa_token === "string" ? fields.mfa_token : null;
    throw new MfaCodeError(message ?? `Sign-in failed (HTTP ${res.status}).`, nextToken);
  }
  return data as AuthResult;
}

export function mfaStatus(): Promise<{ enrolled: boolean }> {
  return call("/auth/mfa/status", undefined, { method: "GET", authenticated: true });
}

/** Start enrolment. `accountName` labels the entry in the authenticator app. */
export function mfaEnroll(accountName?: string): Promise<{ secret: string; otpauth_uri: string }> {
  return call("/auth/mfa/enroll", { account_name: accountName }, { authenticated: true });
}

export async function mfaConfirm(code: string): Promise<void> {
  await call("/auth/mfa/confirm", { code }, { authenticated: true });
}

export async function mfaDisable(code: string): Promise<void> {
  await call("/auth/mfa/disable", { code }, { authenticated: true });
}

/**
 * Exchange a refresh token for a fresh access token (and a rotated refresh
 * token). Throws if the refresh token is invalid, expired, or revoked.
 */
export function refresh(refreshToken: string): Promise<AuthResult> {
  return call("/auth/refresh", { refresh_token: refreshToken });
}

/**
 * Revoke a refresh token server-side (sign-out). Best-effort: never throws, so
 * the UI can always complete a local sign-out even if the network call fails.
 */
export async function logout(refreshToken: string): Promise<void> {
  if (!refreshToken) return;
  try {
    await send("/auth/logout", { refresh_token: refreshToken });
  } catch {
    /* best-effort — local sign-out proceeds regardless */
  }
}
