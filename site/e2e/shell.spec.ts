import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const FIXTURES = ["synthetic", "minimal"] as const;

async function overflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

async function violations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

for (const fixture of FIXTURES) {
  test.describe(`the ${fixture} fixture`, () => {
    test("opens on the list under 720px, without horizontal overflow at 390px", async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(`/?fixture=${fixture}`);

      await expect(page.getByRole("button", { name: "List" })).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByRole("region", { name: /as a list$/ })).toBeVisible();
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });

    test("opens on the graph at desktop width", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/?fixture=${fixture}`);

      await expect(page.getByRole("button", { name: "Graph" })).toHaveAttribute("aria-pressed", "true");
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });

    test("has no axe violations in either view", async ({ page }) => {
      await page.goto(`/?fixture=${fixture}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      expect(await violations(page)).toEqual([]);

      await page.getByRole("button", { name: "List" }).click();
      await expect(page.getByRole("region", { name: /as a list$/ })).toBeVisible();
      expect(await violations(page)).toEqual([]);
    });
  });
}

test("every outcome of the synthetic fixture has no axe violations or overflow at 390px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?fixture=synthetic");
  const tabs = page.getByRole("navigation", { name: "Outcomes" }).getByRole("link");
  for (let i = 0; i < (await tabs.count()); i++) {
    await tabs.nth(i).click();
    await expect(tabs.nth(i)).toHaveAttribute("aria-current", "page");
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    expect(await violations(page)).toEqual([]);
  }
});

test("an outcome tab switches the strip and keeps reviews and the method", async ({ page }) => {
  await page.goto("/?fixture=synthetic");
  await page.getByRole("link", { name: /^Synthetic cases/ }).click();

  await expect(page).toHaveURL(/\/outcome\/Synthetic%20cases\?fixture=synthetic$/);
  await expect(page.getByRole("region", { name: "Summary for synthetic cases" })).toContainText(
    "Thin evidence.",
  );
  await expect(page.getByRole("heading", { name: "Reviews and meta-analyses" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "How this page was made" })).toBeVisible();
});

test("a weighted bar segment says its share on keyboard focus and on hover", async ({ page }) => {
  await page.goto("/?fixture=synthetic");
  const bar = page.getByRole("group", { name: "Design-weighted share by direction" });
  const readout = page.locator(".shell-bar__readout");

  await bar.getByRole("img").first().focus();
  await expect(readout).toHaveText("Benefit: 64% of the design-weighted evidence");

  await bar.getByRole("img").last().hover();
  await expect(readout).toHaveText("Harm: 12% of the design-weighted evidence");
});

test("the finding card keeps its height and scrolls only its middle", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/?fixture=synthetic");
  const card = page.getByRole("complementary", { name: /./ });
  const before = await card.boundingBox();

  await page.getByRole("button", { name: "List" }).click();
  await page.getByRole("button", { name: "Synthetic106 et al. 2016" }).click();
  await expect(card.getByRole("heading")).toHaveText("Synthetic study 106 of supplement X and mood");

  const after = await card.boundingBox();
  expect(after?.height).toBe(before?.height);
  const body = card.getByRole("region", { name: "Finding details" });
  expect(await body.evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");
  await expect(card.getByRole("link", { name: "Open on PubMed" })).toBeInViewport();
});

test("picking another finding scrolls the card back to its claim", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/?fixture=synthetic");
  await page.getByRole("button", { name: "List" }).click();
  await page.getByRole("button", { name: "Synthetic106 et al. 2016" }).click();
  const body = page.getByRole("complementary").getByRole("region", { name: "Finding details" });
  await body.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  expect(await body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

  await page
    .getByRole("region", { name: /as a list$/ })
    .getByRole("button")
    .filter({ hasNotText: "Synthetic106" })
    .first()
    .click();

  await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBe(0);
});
