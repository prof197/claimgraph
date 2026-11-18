import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const SYMPTOMS = "/outcome/Synthetic%20symptoms?fixture=synthetic";
const PAIR = "/pair/Synthetic%20symptoms%7C999000101%7C999000102?fixture=synthetic";

function panel(page: Page) {
  return page.getByRole("region", { name: "Where studies disagree" });
}

test("the keyboard reaches a pair, opens it and comes back", async ({ page }) => {
  await page.goto(SYMPTOMS);
  await expect(panel(page)).toBeVisible();
  let href: string | null = null;
  for (let i = 0; i < 120 && href === null; i += 1) {
    await page.keyboard.press("Tab");
    href = await page.evaluate(() => {
      const el = document.activeElement;
      return el instanceof HTMLAnchorElement && el.pathname.startsWith("/pair/") ? el.pathname : null;
    });
  }
  // The first closely matched pair in the panel's order.
  expect(href).toBe("/pair/Synthetic%20symptoms%7C999000102%7C999000104");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Contradiction · Synthetic symptoms");

  await page.getByRole("banner").getByRole("link", { name: "Back to where studies disagree" }).click();
  await expect(panel(page)).toBeVisible();
});

test("the closely matched pairs sit side by side, and stack without sideways scroll at 390px", async ({ page }) => {
  await page.goto(SYMPTOMS);
  await expect(panel(page).getByRole("link", { name: /^Compare in full\s+for / })).toHaveCount(3);
  await expect(panel(page).getByRole("group", { name: "Filter by likely reason" })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel(page)).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("an outcome with no closely matched disagreement says so and lists the apparent one", async ({ page }) => {
  await page.goto("/outcome/Synthetic%20remission?fixture=synthetic");
  await expect(panel(page).getByText("No closely matched studies on synthetic remission disagree.")).toBeVisible();
  await panel(page).getByText("1 closely matched pair only looked contradictory").click();
  await expect(panel(page).getByText(/underpowered: n = 50/)).toBeVisible();
});

test("the pair page has no axe violations and no sideways scroll at 390px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(PAIR);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
});

test("the panel has no axe violations with the apparent list open", async ({ page }) => {
  await page.goto(SYMPTOMS);
  await panel(page).getByText("1 closely matched pair only looked contradictory").click();
  const results = await new AxeBuilder({ page }).include("section[aria-labelledby]").analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
});

test("an unknown pair says so", async ({ page }) => {
  await page.goto("/pair/nothing?fixture=synthetic");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("No such pair");
});

test("a pair opens at its top, and going back returns to the same place", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 700 });
  await page.goto(SYMPTOMS);
  const link = panel(page).locator('a[href^="/pair/"]').first();
  await link.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  expect(before).toBeGreaterThan(0);
  await link.click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Contradiction");
  expect(await page.evaluate(() => window.scrollY)).toBe(0);

  await page.goBack();
  await expect(panel(page)).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeCloseTo(before, -1);
});

test("the way back under a pair lands on the panel, its heading focused", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 700 });
  await page.goto(PAIR);
  await page.locator("main").getByRole("link", { name: "Back to where studies disagree" }).click();
  const heading = panel(page).getByRole("heading", { name: "Where studies disagree" });
  await expect(heading).toBeFocused();
  expect(await heading.evaluate((el) => Math.abs(el.getBoundingClientRect().top))).toBeLessThan(5);
  expect(new URL(page.url()).hash).toBe("#where-studies-disagree");
});

test("switching outcome tabs keeps the reader's place", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 700 });
  await page.goto(SYMPTOMS);
  const tab = page.getByRole("navigation", { name: "Outcomes" }).getByRole("link").nth(1);
  await page.evaluate(() => window.scrollTo(0, 150));
  const before = await page.evaluate(() => window.scrollY);
  await tab.click();
  await expect(tab).toHaveAttribute("aria-current", "page");
  expect(await page.evaluate(() => window.scrollY)).toBe(before);
});

test("an address from the earlier hash routes opens at its path", async ({ page }) => {
  await page.goto("/?fixture=synthetic#/pair/Synthetic%20symptoms%7C999000101%7C999000102");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Contradiction");
  const url = new URL(page.url());
  expect(url.pathname + url.search + url.hash).toBe(PAIR);
});
