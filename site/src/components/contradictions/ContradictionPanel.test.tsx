import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtures } from "../../fixtures";
import { href } from "../../routes";
import type { ContradictionEdge, Export, Outcome } from "../../types/export";
import { ContradictionPanel, FIRST_PAIRS } from "./ContradictionPanel";

function outcomeOf(data: Export, name: string): Outcome {
  const o = data.outcomes.find((x) => x.name === name);
  if (o === undefined) throw new Error(`no outcome ${name}`);
  return o;
}

/** The synthetic fixture with extra population pairs, copies of its first one. */
function withPopulationPairs(extra: number): Export {
  const data = JSON.parse(JSON.stringify(fixtures.synthetic)) as Export;
  const base = data.graph.edges.find(
    (e): e is ContradictionEdge => e.kind === "contradiction" && e.reason === "population",
  );
  if (base === undefined) throw new Error("the fixture has a population pair");
  for (let i = 0; i < extra; i += 1) {
    data.graph.edges.push({ ...base, pair_id: `${base.pair_id}|copy${i}`, design_weight: 1 });
  }
  return data;
}

const synthetic = fixtures.synthetic;
const symptoms = outcomeOf(synthetic, "Synthetic symptoms");

function panel() {
  return screen.getByRole("region", { name: "Where studies disagree" });
}

describe("the contradiction panel", () => {
  it("genuine pairs: groups them by reason, each linking to its pair page with its detail", () => {
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    expect(within(panel()).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Population1 pair",
      "Intervention1 pair",
      "Comparator1 pair",
      "Outcome measure1 pair",
      "Design or risk of bias1 pair",
      "Unclear1 pair",
    ]);
    const link = within(panel()).getByRole("link", { name: /depressed adults vs a general older population/ });
    expect(link.getAttribute("href")).toBe(href({ page: "pair", pairId: "Synthetic symptoms|999000101|999000102" }));
    expect(link.textContent).toContain("Found a benefit");
    expect(link.textContent).toContain("Synthetic101 2011");
    expect(link.textContent).toContain("Found no effect");
    expect(link.textContent).toContain("n 18,000");
    expect(link.textContent).toContain("Likely reason: population");
    expect(link.textContent).toContain("Also differs: intervention");
    expect(screen.getByText(/6 pairs on synthetic symptoms\. 8 of 16 candidate pairs were classified\./)).toBeTruthy();
  });

  it("filtered by reason: a reason button shows only that group", async () => {
    const user = userEvent.setup();
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    const filters = screen.getByRole("group", { name: "Filter by likely reason" });
    expect(within(filters).getByRole("button", { name: "All · 6" }).getAttribute("aria-pressed")).toBe("true");

    await user.click(within(filters).getByRole("button", { name: "Comparator · 1" }));

    expect(within(filters).getByRole("button", { name: "Comparator · 1" }).getAttribute("aria-pressed")).toBe("true");
    expect(within(panel()).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Comparator1 pair"]);
    expect(within(panel()).getAllByRole("link")).toHaveLength(1);

    await user.click(within(filters).getByRole("button", { name: "All · 6" }));
    expect(within(panel()).getAllByRole("link")).toHaveLength(6);
  });

  it("more pairs than fit: shows the first few per reason, then all of them on request", async () => {
    const user = userEvent.setup();
    const data = withPopulationPairs(4);
    render(<ContradictionPanel data={data} outcome={outcomeOf(data, "Synthetic symptoms")} />);
    const population = within(panel()).getByRole("heading", { name: /^Population/ }).parentElement;
    if (population === null) throw new Error("the group has a container");
    expect(within(population).getAllByRole("link")).toHaveLength(FIRST_PAIRS);

    await user.click(screen.getByRole("button", { name: "Show all 10 pairs" }));

    expect(within(population).getAllByRole("link")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });

  it("apparent pairs: listed collapsed beneath, each with its one-line reason", async () => {
    const user = userEvent.setup();
    const { container } = render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    await user.click(screen.getByText("2 pairs looked contradictory but weren't"));
    expect(details?.open).toBe(true);
    expect(screen.getByText("Synthetic102 2012 vs Synthetic106 2016 · underpowered: a harm seen in 18 people")).toBeTruthy();
  });

  it("no genuine contradictions, with apparent ones: says so and still lists them", () => {
    render(<ContradictionPanel data={synthetic} outcome={outcomeOf(synthetic, "Synthetic remission")} />);
    expect(screen.getByText("No genuine contradictions on synthetic remission.")).toBeTruthy();
    expect(screen.getByText("The classifier read 2 candidate pairs and judged none genuine. 2 of 6 candidate pairs were classified.")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Filter by likely reason" })).toBeNull();
    expect(screen.getByText("2 pairs looked contradictory but weren't")).toBeTruthy();
    expect(screen.getByText(/underpowered: n = 50/)).toBeTruthy();
  });

  it("no genuine contradictions, none classified yet: says the candidates wait", () => {
    render(<ContradictionPanel data={synthetic} outcome={outcomeOf(synthetic, "Synthetic cases")} />);
    expect(screen.getByText("No genuine contradictions on synthetic cases.")).toBeTruthy();
    expect(screen.getByText("Its 3 candidate pairs have not been classified yet.")).toBeTruthy();
    expect(screen.queryByText(/looked contradictory/)).toBeNull();
  });

  it("nothing to compare: a thin outcome whose studies all agree", () => {
    const data = fixtures.minimal;
    render(<ContradictionPanel data={data} outcome={outcomeOf(data, "Synthetic symptoms")} />);
    expect(screen.getByText("No two studies on it point different ways, so there was nothing to compare.")).toBeTruthy();
  });

  it("keyboard: tabbing reaches the filters and then every pair", async () => {
    const user = userEvent.setup();
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    const reached: string[] = [];
    for (let i = 0; i < 20; i += 1) {
      await user.tab();
      const el = document.activeElement;
      if (el instanceof HTMLAnchorElement) reached.push(el.getAttribute("href") ?? "");
    }
    expect(new Set(reached).size).toBe(6);
  });

  it("starts unfiltered again on another outcome", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    await user.click(screen.getByRole("button", { name: "Unclear · 1" }));
    rerender(<ContradictionPanel data={synthetic} outcome={outcomeOf(synthetic, "Synthetic remission")} />);
    rerender(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    expect(screen.getByRole("button", { name: "All · 6" }).getAttribute("aria-pressed")).toBe("true");
  });
});
