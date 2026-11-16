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

test("the graph draws the same coordinates on every load", async ({ page }) => {
  await page.goto(SYMPTOMS);
  await expect(graph(page)).toBeVisible();
  const first = await circles(page);
  expect(first).toHaveLength(8);
  await page.reload();
  await expect(graph(page)).toBeVisible();
  expect(await circles(page)).toEqual(first);
});

test("selecting a dot by mouse marks it and draws its contradictions solid", async ({ page }) => {
  await page.goto(SYMPTOMS);
  const dot = graph(page).getByRole("button", { name: /^Synthetic102 et al. 2012/ });
  await dot.click();
  await expect(dot).toHaveAttribute("aria-pressed", "true");
  await expect(graph(page).locator("[data-selected]")).toHaveAttribute("data-selected", "999000102-1");
});

test("the keyboard reaches the graph in one stop and moves between dots with arrows", async ({ page }) => {
  await page.goto(SYMPTOMS);
  await expect(graph(page)).toBeVisible();
  let inGraph = false;
  for (let i = 0; i < 60 && !inGraph; i += 1) {
    await page.keyboard.press("Tab");
    inGraph = await page.evaluate(() => document.activeElement?.closest("svg") !== null);
  }
  expect(inGraph).toBe(true);
  const before = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  await page.keyboard.press("ArrowRight");
  const after = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  expect(after).not.toBe(before);
  await page.keyboard.press("Enter");
  await expect(page.locator(`[aria-label="${after}"]`)).toHaveAttribute("aria-pressed", "true");
});

for (const outcome of ["Synthetic%20symptoms", "Synthetic%20cases", "Synthetic%20remission"]) {
  test(`the graph for ${decodeURIComponent(outcome)} has no axe violations`, async ({ page }) => {
    await page.goto(`/?fixture=synthetic#/outcome/${outcome}`);
    await expect(graph(page)).toBeVisible();
    const results = await new AxeBuilder({ page }).include("svg[data-testid=graph]").analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });
}

test("a thin outcome names every dot", async ({ page }) => {
  await page.goto("/?fixture=minimal");
  await expect(graph(page).getByText("Synthetic301 et al. 2021")).toBeVisible();
  await expect(graph(page).getByText("Synthetic302 et al. 2021")).toBeVisible();
});
