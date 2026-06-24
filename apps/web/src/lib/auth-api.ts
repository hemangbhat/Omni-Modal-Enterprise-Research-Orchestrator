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
