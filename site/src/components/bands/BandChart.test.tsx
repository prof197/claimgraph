import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtures } from "../../fixtures";
import { studyRows } from "../shell/labels";
import { BandChart } from "./BandChart";

const data = fixtures.synthetic;
const outcome = data.outcomes[0]!;
const rows = studyRows(data, outcome);
const every = data.outcomes.map((o) => studyRows(data, o));

// jsdom lays nothing out, so the panel reports a fixed size the way a browser would.
class FixedSize {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    const entry = { target, contentRect: { width: 760, height: 480 } } as unknown as ResizeObserverEntry;
    this.callback([entry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", FixedSize);
});

afterAll(() => {
  vi.unstubAllGlobals();
});

function chart(selected: string | null = null, onSelect = vi.fn()) {
  render(
    <BandChart outcome={outcome} rows={rows} everyOutcome={every} through={2026} selected={selected} onSelect={onSelect} />,
  );
  return { group: screen.getByRole("group", { name: /as a graph/ }), onSelect };
}

function bandText(group: HTMLElement): string[] {
  return [...group.querySelectorAll("text")].map((t) => t.textContent ?? "");
}

it("draws one named button per study, each starting with its direction", () => {
  const { group } = chart();
  const buttons = within(group).getAllByRole("button");
  expect(buttons).toHaveLength(rows.length);
  for (const b of buttons) {
    expect(b.getAttribute("aria-label")).toMatch(/^[^:]+: (Benefit|No effect|Harm), /);
  }
  expect(within(group).getByRole("button", { name: /^Synthetic108 et al\. 2018: .*subgroup results only, not counted/ })).toBeTruthy();
  expect(within(group).getByRole("button", { name: /^PMID 999000107 \(2017\): .*design without a weight, not counted/ })).toBeTruthy();
});

it("is one tab stop, with arrow keys between dots and Enter to select", async () => {
  const user = userEvent.setup();
  const { group, onSelect } = chart();
  expect(within(group).getAllByRole("button").filter((b) => b.tabIndex === 0)).toHaveLength(1);

  await user.tab();
  const first = document.activeElement;
  expect(group.contains(first)).toBe(true);
  await user.keyboard("{ArrowRight}");
  const second = document.activeElement;
  expect(second).not.toBe(first);
  expect(group.contains(second)).toBe(true);
  await user.keyboard("{ArrowLeft}");
  expect(document.activeElement).toBe(first);
  await user.keyboard("{End}{Enter}");
  expect(onSelect).toHaveBeenCalledTimes(1);
});

it("marks the selected study and makes it the tab stop", () => {
  const { group } = chart("999000104-1");
  const picked = within(group).getByRole("button", { pressed: true });
  expect(picked.getAttribute("aria-label")).toMatch(/^Synthetic104 et al\. 2014/);
  expect(picked.tabIndex).toBe(0);
});

it("reselects through a click", async () => {
  const user = userEvent.setup();
  const { group, onSelect } = chart();
  await user.click(within(group).getByRole("button", { name: /^Synthetic102 et al\. 2012/ }));
  expect(onSelect).toHaveBeenCalledWith("999000102-1");
});

it("recounts the bands for the designs picked in the legend, and shows all again", async () => {
  const user = userEvent.setup();
  const { group } = chart();
  const before = bandText(group);
  expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();

  const legend = screen.getByRole("group", { name: "Highlight designs" });
  const rct = within(legend).getByRole("button", { name: "RCT" });
  expect(rct.getAttribute("title")).toBe("Randomized controlled trial");
  await user.click(rct);
  expect(rct.getAttribute("aria-pressed")).toBe("true");
  const rcts = rows.filter((r) => r.notCounted === null && r.study.design === "rct");
  const benefit = rcts.filter((r) => r.finding.direction === "benefit").length;
  expect(bandText(group)).toContain(`${benefit} ${benefit === 1 ? "study" : "studies"}`);
  expect(bandText(group)).not.toEqual(before);

  await user.click(screen.getByRole("button", { name: "Show all" }));
  expect(bandText(group)).toEqual(before);
});

it("names each band and says when one has no studies", () => {
  const thin = studyRows(data, data.outcomes[1]!);
  render(
    <BandChart outcome={data.outcomes[1]!} rows={thin} everyOutcome={every} through={2026} selected={null} onSelect={vi.fn()} />,
  );
  const text = bandText(screen.getByRole("group", { name: /as a graph/ }));
  for (const name of ["Benefit", "No effect", "Harm"]) expect(text).toContain(name);
  const empty = ["benefit", "none", "harm"].filter((d) => !thin.some((r) => r.finding.direction === d));
  expect(text.filter((t) => t === "No studies")).toHaveLength(empty.length);
});

it("says so when an outcome has nothing to draw", () => {
  render(<BandChart outcome={outcome} rows={[]} everyOutcome={every} through={2026} selected={null} onSelect={vi.fn()} />);
  expect(screen.getByText(/No findings on synthetic symptoms to draw/)).toBeTruthy();
});

it("rules a line between bands, like the year gridlines", () => {
  const { group } = chart();
  const tops = [...group.querySelectorAll("rect[data-band]")].slice(1).map((r) => r.getAttribute("y"));
  const lines = [...group.querySelectorAll("line[data-row-line]")];
  expect(lines.map((l) => l.getAttribute("y1"))).toEqual(tops);
  expect(lines.every((l) => l.getAttribute("y1") === l.getAttribute("y2"))).toBe(true);
});
