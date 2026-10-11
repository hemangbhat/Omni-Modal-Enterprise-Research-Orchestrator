/**
 * Screen-level tests for the forgot-password and reset-password pages.
 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ forgotPassword: vi.fn(), resetPassword: vi.fn() }));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a>
}));
vi.mock("@/lib/auth-api", () => api);

import ForgotPasswordPage from "@/app/forgot-password/page";
import ResetPasswordPage from "@/app/reset-password/page";

function openWithQuery(query: string) {
  window.history.pushState({}, "", `/reset-password${query}`);
}

async function fillPasswords(user: ReturnType<typeof userEvent.setup>, first: string, second: string) {
  await user.type(await screen.findByLabelText("New password"), first);
  await user.type(screen.getByLabelText("Confirm new password"), second);
  await user.click(screen.getByRole("button", { name: /change password/i }));
}

describe("ResetPasswordPage", () => {
  beforeEach(() => {
    api.forgotPassword.mockReset();
    api.resetPassword.mockReset();
  });
  afterEach(cleanup);

  it("explains that the link is invalid when there is no token", async () => {
    openWithQuery("");
    render(<ResetPasswordPage />);

    expect((await screen.findByRole("alert")).textContent).toContain("invalid or has expired");
    expect(screen.queryByLabelText("New password")).toBeNull();
    expect(screen.getByRole("link", { name: /request a new link/i }).getAttribute("href")).toBe(
      "/forgot-password"
    );
  });

  it("changes the password with the token from the link", async () => {
    const user = userEvent.setup();
    api.resetPassword.mockResolvedValue(undefined);
    openWithQuery("?token=tok-123");
    render(<ResetPasswordPage />);

    await fillPasswords(user, "brand-new-pass", "brand-new-pass");

    expect(api.resetPassword).toHaveBeenCalledWith("tok-123", "brand-new-pass");
    expect((await screen.findByRole("status")).textContent).toContain("password has been changed");
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/sign-in");
    expect(screen.queryByLabelText("New password")).toBeNull();
  });

  it("catches a mismatch before calling the backend and does not suggest a new link", async () => {
    const user = userEvent.setup();
    openWithQuery("?token=tok-123");
    render(<ResetPasswordPage />);

    await fillPasswords(user, "brand-new-pass", "different-pass");

    expect((await screen.findByRole("alert")).textContent).toContain("do not match");
    expect(api.resetPassword).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /request a new link/i })).toBeNull();
  });

  it("rejects a short password before calling the backend", async () => {
    const user = userEvent.setup();
    openWithQuery("?token=tok-123");
    render(<ResetPasswordPage />);

    await fillPasswords(user, "short", "short");

    expect((await screen.findByRole("alert")).textContent).toContain("at least 8 characters");
    expect(api.resetPassword).not.toHaveBeenCalled();
  });

  it("offers a new link when the backend refuses the token", async () => {
    const user = userEvent.setup();
    api.resetPassword.mockRejectedValue(new Error("This reset link is invalid or has expired."));
    openWithQuery("?token=stale");
    render(<ResetPasswordPage />);

    await fillPasswords(user, "brand-new-pass", "brand-new-pass");

    expect((await screen.findByRole("alert")).textContent).toContain("invalid or has expired");
    expect(screen.getByRole("link", { name: /request a new link/i })).toBeTruthy();
  });
});

describe("ForgotPasswordPage", () => {
  beforeEach(() => {
    api.forgotPassword.mockReset();
  });
  afterEach(cleanup);

  it("sends the request and shows the same confirmation for any address", async () => {
    const user = userEvent.setup();
    api.forgotPassword.mockResolvedValue(undefined);
    render(<ForgotPasswordPage />);

    await user.type(screen.getByLabelText("Email"), "  user@example.com ");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(api.forgotPassword).toHaveBeenCalledWith("user@example.com");
    expect((await screen.findByRole("status")).textContent).toContain("If an account exists");
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  it("does not call the backend for something that is not an address", async () => {
    const user = userEvent.setup();
    render(<ForgotPasswordPage />);

    // The email input's own validation (or the page's check) stops the submit.
    await user.type(screen.getByLabelText("Email"), "not-an-email");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(api.forgotPassword).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says so when the request could not be sent", async () => {
    const user = userEvent.setup();
    api.forgotPassword.mockRejectedValue(new Error("Network error"));
    render(<ForgotPasswordPage />);

    await user.type(screen.getByLabelText("Email"), "user@example.com");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect((await screen.findByRole("alert")).textContent).toContain("Network error");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
