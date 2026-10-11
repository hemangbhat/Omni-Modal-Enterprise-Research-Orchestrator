/**
 * Unit tests for apps/web/src/lib/auth-api.ts — credential login, the MFA
 * step-up flow, and how backend (FastAPI `detail`) errors reach the UI.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

const SESSION = {
  token: "a.b.c",
  tenant_id: "t1",
  user_id: "u1",
  roles: ["admin"],
  email: "user@example.com",
  expires_at: 1,
  refresh_token: "r1"
};

describe("auth-api client", () => {
  let lastUrl = "";
  let lastInit: RequestInit | undefined;
  let next: () => Response;

  beforeEach(() => {
    lastUrl = "";
    lastInit = undefined;
    next = () => jsonResponse(SESSION);
    window.localStorage.clear();
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      lastUrl = url;
      lastInit = init;
      return next();
    });
  });

  function sentBody(): unknown {
    return JSON.parse(String(lastInit?.body));
  }

  it("login returns the session when the account has no MFA", async () => {
    const { login, isMfaChallenge } = await import("@/lib/auth-api");
    const result = await login("user@example.com", "password123");
    expect(isMfaChallenge(result)).toBe(false);
    expect(result).toMatchObject({ token: "a.b.c", refresh_token: "r1" });
  });

  it("login returns the MFA challenge unchanged when a code is required", async () => {
    next = () => jsonResponse({ mfa_required: true, mfa_token: "nonce-1" });
    const { login, isMfaChallenge } = await import("@/lib/auth-api");
    const result = await login("user@example.com", "password123");
    expect(isMfaChallenge(result)).toBe(true);
    expect(result).toEqual({ mfa_required: true, mfa_token: "nonce-1" });
  });

  it("login surfaces the backend's detail string as the error message", async () => {
    next = () => jsonResponse({ detail: "Invalid email or password." }, 401);
    const { login } = await import("@/lib/auth-api");
    await expect(login("user@example.com", "nope")).rejects.toThrow("Invalid email or password.");
  });

  it("register surfaces the first validation message from a 422 detail list", async () => {
    next = () =>
      jsonResponse({ detail: [{ loc: ["body", "password"], msg: "String should have at least 8 characters" }] }, 422);
    const { register } = await import("@/lib/auth-api");
    await expect(register("user@example.com", "short")).rejects.toThrow(
      "String should have at least 8 characters"
    );
  });

  it("mfaLogin posts the challenge token and code and returns the session", async () => {
    const { mfaLogin } = await import("@/lib/auth-api");
    const result = await mfaLogin("nonce-1", "123456");
    expect(lastUrl).toContain("/auth/mfa/login");
    expect(lastInit?.method).toBe("POST");
    expect(sentBody()).toEqual({ mfa_token: "nonce-1", code: "123456" });
    expect(result.token).toBe("a.b.c");
  });

  it("mfaLogin throws MfaCodeError carrying the next challenge token", async () => {
    next = () => jsonResponse({ detail: { error: "Invalid MFA code.", mfa_token: "nonce-2" } }, 401);
    const { mfaLogin, MfaCodeError } = await import("@/lib/auth-api");
    const err = await mfaLogin("nonce-1", "000000").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(MfaCodeError);
    expect((err as InstanceType<typeof MfaCodeError>).message).toBe("Invalid MFA code.");
    expect((err as InstanceType<typeof MfaCodeError>).nextToken).toBe("nonce-2");
  });

  it("mfaLogin reports a null next token once attempts are exhausted", async () => {
    next = () => jsonResponse({ detail: { error: "Invalid MFA code.", mfa_token: null } }, 401);
    const { mfaLogin, MfaCodeError } = await import("@/lib/auth-api");
    const err = await mfaLogin("nonce-5", "000000").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(MfaCodeError);
    expect((err as InstanceType<typeof MfaCodeError>).nextToken).toBeNull();
  });

  it("mfaLogin reports a null next token when the challenge has expired", async () => {
    next = () => jsonResponse({ detail: "MFA challenge expired or invalid." }, 401);
    const { mfaLogin, MfaCodeError } = await import("@/lib/auth-api");
    const err = await mfaLogin("stale", "123456").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(MfaCodeError);
    expect((err as InstanceType<typeof MfaCodeError>).message).toBe("MFA challenge expired or invalid.");
    expect((err as InstanceType<typeof MfaCodeError>).nextToken).toBeNull();
  });

  it("mfaLogin carries the HTTP status so a lockout can be told from a wrong code", async () => {
    next = () =>
      jsonResponse(
        { detail: { error: "Too many wrong codes. Try again in about 15 minutes.", mfa_token: null } },
        429
      );
    const { mfaLogin, MfaCodeError } = await import("@/lib/auth-api");
    const err = await mfaLogin("nonce-1", "000000").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(MfaCodeError);
    expect((err as InstanceType<typeof MfaCodeError>).status).toBe(429);
    expect((err as InstanceType<typeof MfaCodeError>).message).toBe(
      "Too many wrong codes. Try again in about 15 minutes."
    );
  });

  it("mfaLogin does not report a server failure as a rejected code", async () => {
    next = () => jsonResponse({ detail: "Internal Server Error" }, 500);
    const { mfaLogin, MfaCodeError } = await import("@/lib/auth-api");
    const err = await mfaLogin("nonce-1", "123456").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(MfaCodeError);
  });

  it("mfaEnroll sends the bearer token with the email and current password", async () => {
    window.localStorage.setItem("omni_token", "session.jwt.token");
    next = () => jsonResponse({ secret: "ABC", otpauth_uri: "otpauth://totp/x" });
    const { mfaEnroll } = await import("@/lib/auth-api");
    const result = await mfaEnroll("user@example.com", "password123");
    expect(lastUrl).toContain("/auth/mfa/enroll");
    expect(new Headers(lastInit?.headers).get("Authorization")).toBe("Bearer session.jwt.token");
    expect(sentBody()).toEqual({ email: "user@example.com", password: "password123" });
    expect(result).toEqual({ secret: "ABC", otpauth_uri: "otpauth://totp/x" });
  });

  it("mfaStatus issues an authenticated GET", async () => {
    window.localStorage.setItem("omni_token", "session.jwt.token");
    next = () => jsonResponse({ enrolled: true });
    const { mfaStatus } = await import("@/lib/auth-api");
    expect(await mfaStatus()).toEqual({ enrolled: true });
    expect(lastUrl).toContain("/auth/mfa/status");
    expect(lastInit?.method).toBe("GET");
    expect(new Headers(lastInit?.headers).get("Authorization")).toBe("Bearer session.jwt.token");
  });

  it("mfaConfirm and mfaDisable post the code and reject with the backend message", async () => {
    window.localStorage.setItem("omni_token", "session.jwt.token");
    const { mfaConfirm, mfaDisable } = await import("@/lib/auth-api");
    next = () => jsonResponse({ enrolled: true });
    await mfaConfirm("123456");
    expect(lastUrl).toContain("/auth/mfa/confirm");
    expect(sentBody()).toEqual({ code: "123456" });

    next = () => jsonResponse({ detail: "Invalid code; MFA not disabled." }, 400);
    await expect(mfaDisable("000000")).rejects.toThrow("Invalid code; MFA not disabled.");
    expect(lastUrl).toContain("/auth/mfa/disable");
  });
});

describe("auth-api password reset", () => {
  let lastUrl = "";
  let lastInit: RequestInit | undefined;
  let next: () => Response;

  beforeEach(() => {
    next = () => jsonResponse({ sent: true });
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      lastUrl = url;
      lastInit = init;
      return next();
    });
  });

  it("forgotPassword posts the email to /auth/password/forgot", async () => {
    const { forgotPassword } = await import("@/lib/auth-api");
    await forgotPassword("user@example.com");
    expect(lastUrl).toContain("/auth/password/forgot");
    expect(lastInit?.method).toBe("POST");
    expect(JSON.parse(String(lastInit?.body))).toEqual({ email: "user@example.com" });
    expect(new Headers(lastInit?.headers).get("Authorization")).toBeNull();
  });

  it("resetPassword posts the token and new password to /auth/password/reset", async () => {
    next = () => jsonResponse({ reset: true });
    const { resetPassword } = await import("@/lib/auth-api");
    await resetPassword("tok-123", "brand-new-pass");
    expect(lastUrl).toContain("/auth/password/reset");
    expect(JSON.parse(String(lastInit?.body))).toEqual({ token: "tok-123", password: "brand-new-pass" });
  });

  it("resetPassword surfaces the backend's reason when the link is refused", async () => {
    next = () => jsonResponse({ detail: "This reset link is invalid or has expired." }, 400);
    const { resetPassword } = await import("@/lib/auth-api");
    await expect(resetPassword("stale", "brand-new-pass")).rejects.toThrow(
      "This reset link is invalid or has expired."
    );
  });
});
