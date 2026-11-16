import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("the synthetic fixture renders without horizontal overflow at 390px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?fixture=synthetic");

  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Does synthetic supplement X affect synthetic mood?",
  );
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("the synthetic fixture has no axe violations", async ({ page }) => {
  await page.goto("/?fixture=synthetic");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();

  expect(results.violations.map((v) => v.id)).toEqual([]);
});
