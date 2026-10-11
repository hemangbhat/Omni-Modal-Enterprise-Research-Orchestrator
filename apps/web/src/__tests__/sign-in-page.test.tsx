/**
 * Screen-level tests for the sign-in page: the password step and the
 * authenticator-code step that follows for accounts with two-step sign-in.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  mfaLogin: vi.fn(),
  signIn: vi.fn(),
  replace: vi.fn()
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a>
}));
vi.mock("@/components/auth-context", () => ({
  useAuth: () => ({ signIn: mocks.signIn, isAuthenticated: false, isReady: true })
}));
vi.mock("@/lib/auth-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-api")>("@/lib/auth-api");
  return { ...actual, login: mocks.login, mfaLogin: mocks.mfaLogin };
});

import SignInPage from "@/app/sign-in/page";
import { MfaCodeError } from "@/lib/auth-api";

const SESSION = {
  token: "a.b.c",
  refresh_token: "r1",
  tenant_id: "t1",
  user_id: "u1",
  roles: ["admin"],
  email: "user@example.com",
  expires_at: 1
};

async function submitPassword(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Email"), "user@example.com");
  await user.type(screen.getByLabelText("Password"), "password123");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("SignInPage", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((fn) => fn.mockReset());
    window.localStorage.clear();
  });
  afterEach(cleanup);

  it("signs in straight away when the account has no second step", async () => {
    const user = userEvent.setup();
    mocks.login.mockResolvedValue(SESSION);
    render(<SignInPage />);

    await submitPassword(user);

    await waitFor(() => expect(mocks.signIn).toHaveBeenCalledWith("a.b.c", "r1"));
    expect(mocks.login).toHaveBeenCalledWith("user@example.com", "password123");
    expect(mocks.replace).toHaveBeenCalledWith("/");
    expect(window.localStorage.getItem("omni_email")).toBe("user@example.com");
  });

  it("asks for the authenticator code and finishes sign-in with it", async () => {
    const user = userEvent.setup();
    mocks.login.mockResolvedValue({ mfa_required: true, mfa_token: "nonce-1" });
    mocks.mfaLogin.mockResolvedValue(SESSION);
    render(<SignInPage />);

    await submitPassword(user);
    const code = await screen.findByLabelText("Authenticator code");
    expect(mocks.signIn).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Password")).toBeNull();

    await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: /verify and sign in/i }));

    await waitFor(() => expect(mocks.signIn).toHaveBeenCalledWith("a.b.c", "r1"));
    expect(mocks.mfaLogin).toHaveBeenCalledWith("nonce-1", "123456");
  });

  it("stays on the code step after a wrong code and retries with the new challenge token", async () => {
    const user = userEvent.setup();
    mocks.login.mockResolvedValue({ mfa_required: true, mfa_token: "nonce-1" });
    mocks.mfaLogin
      .mockRejectedValueOnce(new MfaCodeError("Invalid MFA code.", "nonce-2", 401))
      .mockResolvedValueOnce(SESSION);
    render(<SignInPage />);

    await submitPassword(user);
    await user.type(await screen.findByLabelText("Authenticator code"), "000000");
    await user.click(screen.getByRole("button", { name: /verify and sign in/i }));

    expect((await screen.findByRole("alert")).textContent).toContain("not accepted");
    const code = screen.getByLabelText("Authenticator code") as HTMLInputElement;
    expect(code.value).toBe("");

    await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: /verify and sign in/i }));

    await waitFor(() => expect(mocks.signIn).toHaveBeenCalled());
    expect(mocks.mfaLogin).toHaveBeenLastCalledWith("nonce-2", "123456");
  });

  it("returns to the password step when the challenge is over", async () => {
    const user = userEvent.setup();
    mocks.login.mockResolvedValue({ mfa_required: true, mfa_token: "nonce-1" });
    mocks.mfaLogin.mockRejectedValue(new MfaCodeError("Invalid MFA code.", null, 401));
    render(<SignInPage />);

    await submitPassword(user);
    await user.type(await screen.findByLabelText("Authenticator code"), "000000");
    await user.click(screen.getByRole("button", { name: /verify and sign in/i }));

    expect((await screen.findByRole("alert")).textContent).toContain("Sign in again");
    expect(screen.getByLabelText("Password")).toBeTruthy();
    expect(screen.queryByLabelText("Authenticator code")).toBeNull();
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
  });

  it("shows how long the second step is locked for", async () => {
    const user = userEvent.setup();
    mocks.login.mockResolvedValue({ mfa_required: true, mfa_token: "nonce-1" });
    mocks.mfaLogin.mockRejectedValue(
      new MfaCodeError("Too many wrong codes. Try again in about 15 minutes.", null, 429)
    );
    render(<SignInPage />);

    await submitPassword(user);
    await user.type(await screen.findByLabelText("Authenticator code"), "123456");
    await user.click(screen.getByRole("button", { name: /verify and sign in/i }));

    expect((await screen.findByRole("alert")).textContent).toContain("about 15 minutes");
  });

  it("does not call the backend for a code that is not six digits", async () => {
    const user = userEvent.setup();
    mocks.login.mockResolvedValue({ mfa_required: true, mfa_token: "nonce-1" });
    render(<SignInPage />);

    await submitPassword(user);
    await user.type(await screen.findByLabelText("Authenticator code"), "123");
    await user.click(screen.getByRole("button", { name: /verify and sign in/i }));

    expect((await screen.findByRole("alert")).textContent).toContain("6-digit code");
    expect(mocks.mfaLogin).not.toHaveBeenCalled();
  });

  it("shows the backend's message when the password is wrong", async () => {
    const user = userEvent.setup();
    mocks.login.mockRejectedValue(new Error("Invalid email or password."));
    render(<SignInPage />);

    await submitPassword(user);

    expect((await screen.findByRole("alert")).textContent).toContain("Invalid email or password.");
    expect(mocks.signIn).not.toHaveBeenCalled();
  });
});
