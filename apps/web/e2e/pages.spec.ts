import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Every page, at desktop and phone width: no WCAG 2 A/AA violations found by
 * axe-core, and nothing wider than the screen (no sideways scrolling).
 */
const API = "http://localhost:8100";

const PUBLIC_PAGES = ["/sign-in", "/sign-up", "/forgot-password", "/pricing"];
const APP_PAGES = [
  "/", "/upload", "/research", "/documents", "/projects", "/archives",
  "/usage", "/team", "/billing", "/admin", "/settings"
];

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "phone", width: 375, height: 812 }
];

let token = "";

test.beforeAll(async ({ request }) => {
  const res = await request.post(`${API}/auth/register`, {
    data: { email: `pages-${Date.now()}@example.com`, password: "password1234" }
  });
  expect(res.ok()).toBeTruthy();
  token = (await res.json()).token;
});

async function check(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  // Let client-side data arrive and settle into its final state.
  await page.waitForTimeout(500);

  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const violations = results.violations.map((v) => {
    const nodes = v.nodes.slice(0, 4).map((n) => {
      const why = (n.failureSummary ?? "").split("\n").slice(1, 2).join(" ").trim();
      return `      at ${n.target.join(" ")}: ${n.html.slice(0, 160)}\n        ${why}`;
    });
    return `${v.id} (${v.impact}): ${v.help}\n${nodes.join("\n")}`;
  });
  expect(violations, `${path}\n${violations.join("\n")}`).toEqual([]);

  const { overflow, culprits } = await page.evaluate(() => {
    const doc = document.scrollingElement ?? document.documentElement;
    const width = window.innerWidth;
    // The innermost elements that extend past the right edge.
    const wide = Array.from(document.body.querySelectorAll<HTMLElement>("*"))
      .filter((el) => el.getBoundingClientRect().right > width + 1)
      .filter((el) => !Array.from(el.children).some((c) => c.getBoundingClientRect().right > width + 1))
      .slice(0, 5)
      .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 80)}`);
    return { overflow: doc.scrollWidth - width, culprits: wide };
  });
  expect(overflow, `${path} is ${overflow}px wider than the screen:\n${culprits.join("\n")}`).toBeLessThanOrEqual(1);
}

for (const viewport of VIEWPORTS) {
  test.describe(`${viewport.name} width`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    for (const path of PUBLIC_PAGES) {
      test(`public ${path}`, async ({ page }) => {
        await check(page, path);
      });
    }

    for (const path of APP_PAGES) {
      test(`signed in ${path}`, async ({ page }) => {
        await page.addInitScript((t) => window.localStorage.setItem("omni_token", t), token);
        await check(page, path);
      });
    }
  });
}
