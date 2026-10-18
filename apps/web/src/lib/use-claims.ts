"use client";

import { useMemo } from "react";
import { useAuth } from "@/components/auth-context";
import { decodeTokenPayload } from "@/lib/auth";

export type Claims = { tenant_id: string; user_id: string; roles: string[] };

/**
 * Who is signed in, read from the access token, for display only: which
 * buttons to show. The API checks every permission itself.
 */
export function useClaims(): Claims | null {
  const { token } = useAuth();
  return useMemo(() => {
    if (!token) return null;
    const payload = decodeTokenPayload(token);
    if (!payload) return null;
    return {
      tenant_id: String(payload.tenant_id ?? ""),
      user_id: String(payload.user_id ?? ""),
      roles: Array.isArray(payload.roles) ? payload.roles.map(String) : []
    };
  }, [token]);
}
