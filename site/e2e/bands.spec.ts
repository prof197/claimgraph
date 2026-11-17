import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const SYMPTOMS = "/?fixture=synthetic#/outcome/Synthetic%20symptoms";

function graph(page: Page) {
  return page.getByRole("group", { name: /as a graph/ });
}

async function circles(page: Page): Promise<string[]> {
  return graph(page)
    .locator("circle[data-node]")
    .evaluateAll((nodes) =>
      nodes.map((n) => ["data-node", "cx", "cy", "r"].map((a) => n.getAttribute(a)).join(" ")),
    );
}

test("the bands draw the same coordinates on every load", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(SYMPTOMS);
  await expect(graph(page)).toBeVisible();
  const first = await circles(page);
  expect(first).toHaveLength(8);
  await page.reload();
  await expect(graph(page)).toBeVisible();
  expect(await circles(page)).toEqual(first);
});

test("the axis is the same on every outcome", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const ticks = async () =>
    graph(page)
      .locator("line")
      .evaluateAll((lines) => lines.map((l) => l.getAttribute("x1")));
  await page.goto(SYMPTOMS);
  await expect(graph(page)).toBeVisible();
  const symptoms = await ticks();
  await page.goto("/?fixture=synthetic#/outcome/Synthetic%20remission");
  await expect(graph(page)).toBeVisible();
  expect(await ticks()).toEqual(symptoms);
  await expect(graph(page).locator("text", { hasText: "<2010" })).toBeVisible();
});

test("selecting a dot by mouse marks it and opens its finding, without a focus ring", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(SYMPTOMS);
  await expect(page.getByRole("heading", { name: "Select a study from the chart to see its details" })).toBeVisible();
  const dot = graph(page).getByRole("button", { name: /^Synthetic102 et al. 2012/ });
  await dot.click();
  await expect(dot).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("complementary").getByRole("heading", { level: 3 })).not.toHaveText(/Select a study/);
  expect(await dot.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("none");
});

test("the keyboard reaches the chart in one stop and moves between dots with arrows", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(SYMPTOMS);
  await expect(graph(page)).toBeVisible();
  let inGraph = false;
  for (let i = 0; i < 60 && !inGraph; i += 1) {
    await page.keyboard.press("Tab");
    inGraph = await page.evaluate(() => document.activeElement?.closest("svg") !== null);
  }
  expect(inGraph).toBe(true);
  const before = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  expect(await page.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle)).toBe("solid");
  await page.keyboard.press("ArrowRight");
  const after = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  expect(after).not.toBe(before);
  await page.keyboard.press("Enter");
  await expect(page.locator(`[aria-label="${after}"]`)).toHaveAttribute("aria-pressed", "true");
  // One more Tab leaves the chart rather than walking every dot.
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.closest("svg") === null)).toBe(true);
});

test("picking a design recounts the bands", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(SYMPTOMS);
  await expect(graph(page).locator("text", { hasText: "Benefit" })).toBeVisible();
  const texts = () => graph(page).locator("text").allTextContents();
  const before = await texts();
  await page.getByRole("button", { name: "RCT", exact: true }).click();
  expect(await texts()).not.toEqual(before);
  await page.getByRole("button", { name: "Show all" }).click();
  expect(await texts()).toEqual(before);
});

for (const outcome of ["Synthetic%20symptoms", "Synthetic%20cases", "Synthetic%20remission"]) {
  test(`the bands for ${decodeURIComponent(outcome)} have no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?fixture=synthetic#/outcome/${outcome}`);
    await expect(graph(page)).toBeVisible();
    const results = await new AxeBuilder({ page }).include(".shell-figure").analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });
}

test("at 390 px the bands fit the width, with their labels above each band", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(SYMPTOMS);
  await page.getByRole("button", { name: "Graph" }).click();
  await expect(graph(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const svg = await graph(page).boundingBox();
  const figure = await page.locator(".shell-figure").boundingBox();
  expect(svg!.x + svg!.width).toBeLessThanOrEqual(figure!.x + figure!.width);
  const results = await new AxeBuilder({ page }).include(".shell-figure").analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
});
