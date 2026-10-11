/**
 * Screen-level tests for the two-step sign-in settings panel: turning it on
 * (password, QR code, confirmation code) and turning it off.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  mfaStatus: vi.fn(),
  mfaEnroll: vi.fn(),
  mfaConfirm: vi.fn(),
  mfaDisable: vi.fn()
}));

vi.mock("@/lib/auth-api", () => api);
vi.mock("qrcode", () => ({
  default: { toDataURL: vi.fn(async (uri: string) => `data:image/png;base64,QR(${uri})`) }
}));

import { MfaSettings } from "@/components/mfa-settings";

describe("MfaSettings", () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    window.localStorage.clear();
    window.localStorage.setItem("omni_email", "user@example.com");
  });
  afterEach(cleanup);

  it("turns two-step sign-in on: password, then QR code, then confirmation code", async () => {
    const user = userEvent.setup();
    api.mfaStatus.mockResolvedValue({ enrolled: false });
    api.mfaEnroll.mockResolvedValue({ secret: "JBSWY3DP", otpauth_uri: "otpauth://totp/OMERO" });
    api.mfaConfirm.mockResolvedValue(undefined);
    render(<MfaSettings />);

    await user.click(await screen.findByRole("button", { name: /turn on two-step sign-in/i }));

    // The account email is pre-filled; the current password is required.
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("user@example.com");
    await user.type(screen.getByLabelText("Current password"), "password123");
    await user.click(screen.getByRole("button", { name: /continue/i }));

    expect(api.mfaEnroll).toHaveBeenCalledWith("user@example.com", "password123");
    const qr = (await screen.findByAltText(/qr code/i)) as HTMLImageElement;
    expect(qr.src).toContain("otpauth://totp/OMERO");
    expect(screen.getByText("JBSWY3DP")).toBeTruthy();

    await user.type(screen.getByLabelText("Code from the app"), "123456");
    await user.click(screen.getByRole("button", { name: /confirm and turn on/i }));

    expect(api.mfaConfirm).toHaveBeenCalledWith("123456");
    expect(await screen.findByText("On")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Two-step sign-in is on");
  });

  it("shows the backend's reason and stays on the password step when the password is wrong", async () => {
    const user = userEvent.setup();
    api.mfaStatus.mockResolvedValue({ enrolled: false });
    api.mfaEnroll.mockRejectedValue(new Error("Enter your current password to turn on two-step sign-in."));
    render(<MfaSettings />);

    await user.click(await screen.findByRole("button", { name: /turn on two-step sign-in/i }));
    await user.type(screen.getByLabelText("Current password"), "wrong");
    await user.click(screen.getByRole("button", { name: /continue/i }));

    expect((await screen.findByRole("alert")).textContent).toContain("Enter your current password");
    expect(screen.queryByAltText(/qr code/i)).toBeNull();
    expect(screen.getByLabelText("Current password")).toBeTruthy();
  });

  it("does not confirm until six digits are entered, and ignores non-digits", async () => {
    const user = userEvent.setup();
    api.mfaStatus.mockResolvedValue({ enrolled: false });
    api.mfaEnroll.mockResolvedValue({ secret: "JBSWY3DP", otpauth_uri: "otpauth://totp/OMERO" });
    render(<MfaSettings />);

    await user.click(await screen.findByRole("button", { name: /turn on two-step sign-in/i }));
    await user.type(screen.getByLabelText("Current password"), "password123");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    const code = (await screen.findByLabelText("Code from the app")) as HTMLInputElement;
    const confirm = screen.getByRole("button", { name: /confirm and turn on/i }) as HTMLButtonElement;

    await user.type(code, "12a3 4");
    expect(code.value).toBe("1234");
    expect(confirm.disabled).toBe(true);
    await user.type(code, "56");
    expect(confirm.disabled).toBe(false);
  });

  it("turns two-step sign-in off with a current code", async () => {
    const user = userEvent.setup();
    api.mfaStatus.mockResolvedValue({ enrolled: true });
    api.mfaDisable.mockResolvedValue(undefined);
    render(<MfaSettings />);

    expect(await screen.findByText("On")).toBeTruthy();
    await user.type(screen.getByLabelText(/to turn it off/i), "654321");
    await user.click(screen.getByRole("button", { name: /turn off/i }));

    expect(api.mfaDisable).toHaveBeenCalledWith("654321");
    expect(await screen.findByText("Off")).toBeTruthy();
    expect(screen.getByRole("button", { name: /turn on two-step sign-in/i })).toBeTruthy();
  });

  it("keeps it on and shows the reason when the turn-off code is rejected", async () => {
    const user = userEvent.setup();
    api.mfaStatus.mockResolvedValue({ enrolled: true });
    api.mfaDisable.mockRejectedValue(new Error("Invalid code; MFA not disabled."));
    render(<MfaSettings />);

    await user.type(await screen.findByLabelText(/to turn it off/i), "000000");
    await user.click(screen.getByRole("button", { name: /turn off/i }));

    expect((await screen.findByRole("alert")).textContent).toContain("Invalid code");
    expect(screen.getByText("On")).toBeTruthy();
  });

  it("offers a retry when the status cannot be loaded", async () => {
    const user = userEvent.setup();
    api.mfaStatus.mockRejectedValueOnce(new Error("HTTP 503")).mockResolvedValueOnce({ enrolled: false });
    render(<MfaSettings />);

    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 503");
    await user.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("Off")).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
