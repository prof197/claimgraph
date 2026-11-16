import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const SYMPTOMS = "/?fixture=synthetic#/outcome/Synthetic%20symptoms";
const PAIR = "/?fixture=synthetic#/pair/Synthetic%20symptoms%7C999000101%7C999000102";

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
      return el instanceof HTMLAnchorElement && el.hash.startsWith("#/pair/") ? el.hash : null;
    });
  }
  expect(href).toBe("#/pair/Synthetic%20symptoms%7C999000101%7C999000102");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("One trial found a benefit.");

  await page.getByRole("link", { name: "Back to all contradictions" }).click();
  await expect(panel(page)).toBeVisible();
});

test("the reason filter shows one group at a time", async ({ page }) => {
  await page.goto(SYMPTOMS);
  const filters = page.getByRole("group", { name: "Filter by likely reason" });
  await filters.getByRole("button", { name: "Outcome measure · 1" }).click();
  await expect(panel(page).getByRole("heading", { level: 3 })).toHaveText(["Outcome measure1 pair"]);
  await expect(panel(page).getByRole("link")).toHaveCount(1);
});

test("an outcome with no genuine contradictions says so and lists the apparent ones", async ({ page }) => {
  await page.goto("/?fixture=synthetic#/outcome/Synthetic%20remission");
  await expect(panel(page).getByText("No genuine contradictions on synthetic remission.")).toBeVisible();
  await panel(page).getByText("2 pairs looked contradictory but weren't").click();
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
  await panel(page).getByText("2 pairs looked contradictory but weren't").click();
  const results = await new AxeBuilder({ page }).include("section[aria-labelledby]").analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
});

test("an unknown pair says so", async ({ page }) => {
  await page.goto("/?fixture=synthetic#/pair/nothing");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("No such pair");
});
