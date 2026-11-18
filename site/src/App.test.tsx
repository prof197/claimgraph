import { act, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";
import { fixtures } from "./fixtures";
import { navigate, upgradeLegacyAddress } from "./lib/useRoute";
import { href } from "./routes";

const data = fixtures.synthetic;
const PAIR = href({ page: "pair", pairId: "Synthetic symptoms|999000101|999000102" });

let scrolledTo: number[] = [];
beforeEach(() => {
  window.history.replaceState(null, "", "/");
  scrolledTo = [];
  // jsdom does not scroll; the calls record where the page would go.
  vi.spyOn(window, "scrollTo").mockImplementation(((_x: number, y: number) => {
    scrolledTo.push(y);
    Object.defineProperty(window, "scrollY", { value: y, configurable: true });
  }) as typeof window.scrollTo);
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function open() {
  render(<App load={() => Promise.resolve(data)} />);
  await screen.findByRole("heading", { level: 1 });
}

function scrolled(y: number) {
  Object.defineProperty(window, "scrollY", { value: y, configurable: true });
}

describe.each(Object.entries(fixtures))("the %s fixture", (_, data) => {
  it("renders its question page", async () => {
    render(<App load={() => Promise.resolve(data)} />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveProperty(
      "textContent",
      data.question.title,
    );
    expect(screen.getByRole("navigation", { name: "Outcomes" })).toBeTruthy();
  });
});

it("opens a pair page from its route", async () => {
  await open();
  act(() => navigate(PAIR));
  expect((await screen.findByRole("heading", { level: 1 })).textContent).toContain("Contradiction");
});

it("follows an in-site link without a reload, and the new page starts at its top", async () => {
  await open();
  scrolled(1200);
  const link = screen.getAllByRole("link").find((a) => a.getAttribute("href") === PAIR);
  if (link === undefined) throw new Error("the panel links its pairs");
  act(() => {
    fireEvent.click(link);
  });
  expect(window.location.pathname).toBe(PAIR);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Contradiction");
  expect(scrolledTo).toEqual([0]);
});

it("keeps the reader's place when switching outcome tabs", async () => {
  await open();
  scrolled(600);
  act(() => navigate(href({ page: "question", outcome: "Synthetic remission" })));
  const tabs = screen.getByRole("navigation", { name: "Outcomes" });
  expect(tabs.querySelector('[aria-current="page"]')?.textContent).toContain("Synthetic remission");
  expect(scrolledTo).toEqual([]);
});

it("going back returns to where the reader was", async () => {
  await open();
  scrolled(900);
  act(() => navigate(PAIR));
  act(() => {
    // The browser restores the earlier entry's address and state, then says so.
    window.history.replaceState({ scroll: 900 }, "", "/");
    window.dispatchEvent(new PopStateEvent("popstate", { state: { scroll: 900 } }));
  });
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(data.question.title);
  expect(scrolledTo).toEqual([0, 900]);
});

it("opens an address from the earlier hash routes at its path", () => {
  window.history.replaceState(null, "", "/#/pair/Synthetic%20symptoms%7C999000101%7C999000102");
  upgradeLegacyAddress();
  expect(window.location.pathname + window.location.hash).toBe(PAIR);
});
