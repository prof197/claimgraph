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
