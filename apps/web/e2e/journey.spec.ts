import { expect, test } from "@playwright/test";
import path from "node:path";

/**
 * The product journey from the vision: sign up, upload, watch processing, ask,
 * see a cited answer, save it to a project, and generate a report from it.
 */
test("sign up, upload, research, save to a project and report", async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`;

  // Sign up.
  await page.goto("/sign-up");
  await page.getByPlaceholder("you@company.com").fill(email);
  await page.getByPlaceholder("Password (min 8 characters)").fill("password1234");
  await page.getByPlaceholder("Confirm password").fill("password1234");
  await page.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/$/);

  // Upload and watch it process.
  await page.goto("/upload");
  await page
    .locator('input[type="file"]')
    .setInputFiles(path.join(__dirname, "fixtures", "vendor-contract.pdf"));
  const uploads = page.getByRole("region", { name: "Uploads" });
  await expect(uploads.getByText("vendor-contract.pdf")).toBeVisible();
  // Wait for processing to finish ("Ready" is also the name of the last step, so match the phase).
  await expect(uploads.locator('li[data-phase="done"]')).toHaveCount(1, { timeout: 60_000 });

  // A project holding the document.
  await page.goto("/projects");
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByPlaceholder("Q3 board review").fill("Vendor renewals");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/[^/]+$/);
  const projectUrl = page.url();
  await page.getByLabel("Add a document").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("button", { name: /Remove vendor-contract/ })).toBeVisible();

  // Ask inside the project and get a cited answer.
  await page.getByRole("link", { name: "Research to find more" }).click();
  await expect(page).toHaveURL(/\/research\?project=/);
  await page.getByPlaceholder("Ask about your documents…").fill("How much notice ends the hosting contract?");
  await page.getByRole("button", { name: "Ask" }).click();
  // The answer quotes the uploaded contract and cites it. (Which sentence the
  // no-LLM path quotes is measured by the retrieval evaluation, not here.)
  await expect(page.getByText(/Nimbus Cloud/).first()).toBeVisible({ timeout: 60_000 });
  const evidence = page.getByLabel("Evidence");
  await expect(evidence.getByText(/vendor-contract/).first()).toBeVisible();

  // Save the answer to the project.
  await page.getByRole("button", { name: "Save to Vendor renewals" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved to Vendor renewals" })).toBeVisible();

  // Generate a report from the project's findings.
  await page.goto(projectUrl);
  await expect(page.getByText("How much notice ends the hosting contract?").first()).toBeVisible();
  await page.getByRole("button", { name: "Generate report" }).click();
  const report = page.getByRole("dialog");
  await expect(report).toBeVisible();
  await expect(report.getByText("Findings", { exact: true })).toBeVisible();
  await expect(report.getByText("Sources", { exact: true })).toBeVisible();
  await expect(report.getByText(/vendor-contract/).first()).toBeVisible();
  await expect(report.getByRole("link", { name: /Download \.md/ }).or(report.getByRole("button", { name: /Download \.md/ }))).toBeVisible();
});
