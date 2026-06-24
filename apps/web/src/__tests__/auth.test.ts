/**
 * Unit tests for apps/web/src/lib/auth.ts — the client session token store
 * and JWT helpers that back sign-in / sign-out.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  clearStoredToken,
  decodeTokenPayload,
  getDemoToken,
  getStoredToken,
  isTokenExpired,
  setStoredToken
} from "@/lib/auth";

/** Build an unsigned JWT-shaped string with the given payload (for tests). */
function makeToken(payload: Record<string, unknown>): string {
  const b64url = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url(payload)}.sig`;
}

describe("token storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("returns null when nothing is stored", () => {
    expect(getStoredToken()).toBeNull();
  });

  it("round-trips a stored token", () => {
    setStoredToken("abc.def.ghi");
    expect(getStoredToken()).toBe("abc.def.ghi");
  });

  it("clears a stored token", () => {
    setStoredToken("abc.def.ghi");
    clearStoredToken();
    expect(getStoredToken()).toBeNull();
  });
});

describe("decodeTokenPayload", () => {
  it("decodes a well-formed JWT payload", () => {
    const token = makeToken({ user_id: "u1", tenant_id: "demo-tenant", roles: ["admin"] });
    const payload = decodeTokenPayload(token);
    expect(payload?.user_id).toBe("u1");
    expect(payload?.tenant_id).toBe("demo-tenant");
    expect(payload?.roles).toEqual(["admin"]);
  });

  it("returns null for a malformed token", () => {
    expect(decodeTokenPayload("not-a-jwt")).toBeNull();
  });
});

describe("isTokenExpired", () => {
  it("returns true for a token whose exp is in the past", () => {
    const token = makeToken({ exp: Math.floor(Date.now() / 1000) - 60 });
    expect(isTokenExpired(token)).toBe(true);
  });

  it("returns false for a token whose exp is in the future", () => {
    const token = makeToken({ exp: Math.floor(Date.now() / 1000) + 3600 });
    expect(isTokenExpired(token)).toBe(false);
  });

  it("treats tokens without exp as non-expired", () => {
    const token = makeToken({ user_id: "u1" });
    expect(isTokenExpired(token)).toBe(false);
  });
});

describe("getDemoToken", () => {
  const originalEnv = process.env;
  beforeEach(() => {
    process.env = { ...originalEnv };
  });
  afterEach(() => {
    process.env = originalEnv;
    vi.resetModules();
  });

  it("returns null when NEXT_PUBLIC_API_TOKEN is not set", () => {
    delete process.env.NEXT_PUBLIC_API_TOKEN;
    expect(getDemoToken()).toBeNull();
  });
});
