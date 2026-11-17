// The page's accessibility rules, checked in the browser: a keyboard reaches every part of the
// page in order, every control in the tab order is a real button or link, focus is visible, and
// direction is carried by position and labels as well as colour. Text contrast is axe's
// color-contrast rule, run at each stop of the keyboard path.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const HOME = "/?fixture=synthetic";

interface Focused {
  tag: string;
  role: string | null;
  name: string;
  href: string | null;
  inside: string | null;
  outline: string;
}

/** What has focus: its element, role, accessible name as written in the markup, and the nearest
 * labelled landmark or group around it. */
async function focused(page: Page): Promise<Focused | null> {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (el === null || el === document.body) return null;
    const around = el.parentElement?.closest("[aria-label], [aria-labelledby]");
    const labelOf = (node: Element | null | undefined): string | null => {
      if (node === null || node === undefined) return null;
      const by = node.getAttribute("aria-labelledby");
      if (by !== null) return document.getElementById(by)?.textContent?.trim() ?? null;
      return node.getAttribute("aria-label");
    };
    const style = getComputedStyle(el);
    return {
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role"),
      name: (el.getAttribute("aria-label") ?? el.textContent ?? "").trim().replace(/\s+/g, " "),
      href: el instanceof HTMLAnchorElement ? el.getAttribute("href") : null,
      inside: labelOf(around),
      outline: style.outlineStyle === "none" ? style.boxShadow : `${style.outlineStyle} ${style.outlineWidth}`,
    };
  });
}

/** Presses Tab until focus satisfies `want`, and returns every stop on the way, the last one
 * included. Fails if focus never gets there. */
async function tabTo(page: Page, want: (f: Focused) => boolean, limit = 80): Promise<Focused[]> {
  const stops: Focused[] = [];
  for (let i = 0; i < limit; i += 1) {
    await page.keyboard.press("Tab");
    const now = await focused(page);
    if (now !== null) {
      stops.push(now);
      if (want(now)) return stops;
    }
  }
  throw new Error(`never reached the target in ${limit} tabs; went through:\n${describe(stops)}`);
}

function describe(stops: Focused[]): string {
  return stops.map((s) => `${s.tag}${s.role ? `[${s.role}]` : ""} "${s.name}" in ${s.inside}`).join("\n");
}

/** A stop in the tab order is a control only when it is a native button, link or disclosure.
 * Focusable non-controls are allowed for two things: the weighted bar's segments, which say
 * their share on focus, and a scrolling region, which a keyboard must reach to scroll. */
function notRealControls(stops: Focused[]): string[] {
  return stops
    .filter((s) => {
      if (s.tag === "button" || s.tag === "summary" || (s.tag === "a" && s.href !== null)) return false;
      if (s.role === "img" && s.inside === "Design-weighted share by direction") return false;
      if (s.role === "region" && s.name !== "") return false;
      return true;
    })
    .map((s) => describe([s]));
}

async function axe(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

test("a keyboard reaches the tabs, summary, list, finding card and pairs, and back", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(HOME);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const path: Focused[] = [];

  // The outcome tabs are links; Enter on one opens its outcome.
  path.push(...(await tabTo(page, (f) => f.inside === "Outcomes" && f.name.startsWith("Synthetic cases"))));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/outcome\/Synthetic%20cases$/);
  await expect(page.getByRole("link", { name: /^Synthetic cases/ })).toHaveAttribute("aria-current", "page");
  // Back to the first outcome, which has the weighted bar and contradictions.
  path.push(...(await tabTo(page, (f) => f.inside === "Outcomes" && f.name.startsWith("Synthetic symptoms"))));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/outcome\/Synthetic%20symptoms$/);

  // The summary's weighted bar says each segment's share on focus.
  path.push(...(await tabTo(page, (f) => f.inside === "Design-weighted share by direction")));
  await expect(page.locator(".shell-bar__readout")).toHaveText(/^Benefit: \d+% of the design-weighted evidence$/);
  expect(await axe(page)).toEqual([]);

  // The graph or list toggle.
  path.push(...(await tabTo(page, (f) => f.inside === "Show the findings as" && f.name === "List")));
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "List" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("region", { name: /as a list$/ })).toBeVisible();

  // A list row selects its finding, which the card then shows.
  path.push(...(await tabTo(page, (f) => f.tag === "button" && f.name === "Synthetic106 et al. 2016")));
  await page.keyboard.press("Enter");
  const card = page.getByRole("complementary");
  await expect(card.getByRole("heading")).toHaveText("Synthetic study 106 of supplement X and mood");
  await expect(page.getByRole("button", { name: "Synthetic106 et al. 2016" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(await axe(page)).toEqual([]);

  // The card's details scroll from the keyboard, and its actions are links.
  path.push(...(await tabTo(page, (f) => f.role === "region" && f.name === "Finding details")));
  const details = card.getByRole("region", { name: "Finding details" });
  expect(await details.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  await page.keyboard.press("PageDown");
  await expect.poll(() => details.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  path.push(...(await tabTo(page, (f) => f.tag === "a" && f.name.startsWith("Open on PubMed"))));

  // The contradiction pairs are links to their pair pages.
  path.push(...(await tabTo(page, (f) => f.tag === "a" && (f.href ?? "").startsWith("#/pair/"))));
  const pairHref = (path.at(-1) as Focused).href;
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL((url) => url.hash === pairHref);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(await axe(page)).toEqual([]);

  // The pair page's first stop leads back.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const back = await tabTo(page, (f) => f.name === "Back to all contradictions", 3);
  path.push(...back);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/outcome\/Synthetic%20symptoms\/contradictions$/);
  // It lands on the contradictions, focus on their heading, not at the top of the page.
  const heading = page.getByRole("heading", { name: "Where studies disagree" });
  await expect(heading).toBeFocused();
  await expect(heading).toBeInViewport();

  expect(notRealControls(path)).toEqual([]);
  // Every stop shows where focus is.
  expect(path.filter((s) => s.outline === "none" || s.outline === "").map((s) => describe([s]))).toEqual([]);
});

/** Elements that act like controls without being native ones: an interactive role on a
 * non-native element, a focusable element that is neither a native control nor one of the two
 * reading stops, or anything drawn with a pointer cursor outside a native control. */
async function fakeControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const native = "button, a[href], summary, input, select, textarea, label";
    const roles =
      "[role=button], [role=link], [role=tab], [role=checkbox], [role=switch], [role=menuitem], [role=option]";
    const found: string[] = [];
    for (const el of document.querySelectorAll(`${roles}, [onclick], [tabindex]`)) {
      if (el.matches(native)) continue;
      const tabindex = el.getAttribute("tabindex");
      const role = el.getAttribute("role");
      // Focusable for reading, not for acting: the bar's segments and scrolling regions.
      if (tabindex === "0" && (role === "img" || role === "region")) continue;
      if (tabindex === "-1" && !el.matches(roles)) continue;
      found.push(el.outerHTML.slice(0, 120));
    }
    for (const el of document.querySelectorAll("body *")) {
      if (getComputedStyle(el).cursor === "pointer" && el.closest(native) === null) {
        found.push(`pointer: ${el.outerHTML.slice(0, 120)}`);
      }
    }
    return found;
  });
}

test("every control is a real button or link, in both views and on the pair page", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(HOME);
  await expect(page.getByRole("group", { name: /as a graph/ })).toBeVisible();
  expect(await fakeControls(page), "graph view").toEqual([]);

  await page.getByRole("button", { name: "List" }).click();
  await expect(page.getByRole("region", { name: /as a list$/ })).toBeVisible();
  expect(await fakeControls(page), "list view").toEqual([]);

  await page.goto(`${HOME}#/pair/Synthetic%20symptoms%7C999000101%7C999000102`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(await fakeControls(page), "pair page").toEqual([]);

  // The check itself catches a clickable div and a pointer-only span.
  await page.evaluate(() => {
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div role="button" tabindex="0">Fake</div><span style="cursor: pointer">Also fake</span>',
    );
  });
  expect(await fakeControls(page)).toHaveLength(2);
});

test("direction is carried by position and labels as well as colour", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${HOME}#/outcome/Synthetic%20symptoms`);
  const graph = page.getByRole("group", { name: /as a graph/ });
  await expect(graph).toBeVisible();

  // In the graph, each dot's name starts with its direction and the dot sits inside that
  // direction's band: benefit on top, no effect in the middle, harm at the bottom.
  const dots = await graph.locator("foreignObject").evaluateAll((objects) => {
    const svg = objects[0]?.closest("svg");
    const bands = Object.fromEntries(
      [...(svg?.querySelectorAll("rect[data-band]") ?? [])].map((r) => [
        r.getAttribute("data-band"),
        { top: Number(r.getAttribute("y")), bottom: Number(r.getAttribute("y")) + Number(r.getAttribute("height")) },
      ]),
    );
    return objects.map((o) => ({
      name: o.querySelector("button")?.getAttribute("aria-label") ?? "",
      y: Number(o.getAttribute("y")) + Number(o.getAttribute("height")) / 2,
      bands,
    }));
  });
  expect(dots.length).toBeGreaterThan(0);
  const band: Record<string, string> = { Benefit: "benefit", "No effect": "none", Harm: "harm" };
  const misplaced = dots.filter((d) => {
    const direction = /^[^:]+: (Benefit|No effect|Harm),/.exec(d.name)?.[1];
    const zone = direction === undefined ? undefined : d.bands[band[direction] ?? ""];
    return zone === undefined || d.y < zone.top || d.y > zone.bottom;
  });
  expect(misplaced).toEqual([]);
  const tops = ["benefit", "none", "harm"].map((d) => dots[0]?.bands[d]?.top ?? NaN);
  expect(tops).toEqual([...tops].sort((x, y) => x - y));
  // Each band says its direction in words.
  for (const word of ["Benefit", "No effect", "Harm"]) {
    await expect(graph.locator("text", { hasText: word }).first()).toBeVisible();
  }

  // The weighted bar keeps a fixed order, and each segment names its direction.
  const segments = await page
    .getByRole("group", { name: "Design-weighted share by direction" })
    .getByRole("img")
    .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")?.split(":")[0]));
  const order = ["Benefit", "No effect", "Harm"];
  expect(segments).toEqual(order.filter((d) => segments.includes(d)));

  // In the list, each row sits under a heading naming its direction.
  await page.getByRole("button", { name: "List" }).click();
  const groups = await page
    .getByRole("region", { name: /as a list$/ })
    .locator("tbody")
    .evaluateAll((bodies) =>
      bodies.map((b) => ({
        heading: b.querySelector("th[scope=rowgroup]")?.textContent ?? "",
        rows: b.querySelectorAll("button[aria-pressed]").length,
      })),
    );
  expect(groups.length).toBeGreaterThan(0);
  for (const group of groups) {
    expect(group.heading).toMatch(/^(Benefit|No effect|Harm) · /);
    expect(group.rows).toBeGreaterThan(0);
  }
});
