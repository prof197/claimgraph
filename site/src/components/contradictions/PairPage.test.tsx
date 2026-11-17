import { render, screen, within } from "@testing-library/react";
import { fixtures } from "../../fixtures";
import { href } from "../../routes";
import type { ContradictionEdge, Export, FindingNode } from "../../types/export";
import { PairPage } from "./PairPage";

const POPULATION = "Synthetic symptoms|999000101|999000102";

function statusOf(rowName: RegExp): string | null | undefined {
  const row = screen.getByRole("rowheader", { name: rowName }).closest("tr");
  return row?.querySelector("td:last-child")?.textContent;
}

function copy(): Export {
  return JSON.parse(JSON.stringify(fixtures.synthetic)) as Export;
}

describe("the pair page", () => {
  it("shows both findings side by side, the effect first, with the likely reason", () => {
    render(<PairPage data={fixtures.synthetic} pairId={POPULATION} />);
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toBe("Contradiction · Synthetic symptomsOne trial found a benefit. One found no effect.");
    expect(screen.getByText("Likely reason: population")).toBeTruthy();
    expect(screen.getByText("Also differs: intervention")).toBeTruthy();
    expect(screen.getByText("synthetic: depressed adults vs a general older population")).toBeTruthy();

    const cards = within(screen.getByRole("region", { name: "The two findings" })).getAllByRole("article");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Synthetic101 et al. 2011", "Synthetic102 et al. 2012"]);
    const first = cards[0];
    if (first === undefined) throw new Error("two cards");
    expect(within(first).getByText("Found a benefit")).toBeTruthy();
    expect(within(first).getByText("Synthetic sentence 1 from synthetic abstract 999000101.")).toBeTruthy();
    expect(within(first).getByRole("link", { name: /Open on PubMed/ }).getAttribute("href")).toBe(
      "https://pubmed.ncbi.nlm.nih.gov/999000101/",
    );
  });

  it("marks each reason field and custom dimension: likely reason, also differs, different, same", () => {
    render(<PairPage data={fixtures.synthetic} pairId={POPULATION} />);
    // The synthetic populations read the same, yet the classifier named population.
    expect(statusOf(/^Population$/)).toBe("Likely reason");
    expect(statusOf(/^Intervention$/)).toBe("Different · also differs");
    expect(statusOf(/^Comparator$/)).toBe("Same");
    expect(statusOf(/^Design or risk of bias$/)).toBe("Same");
    expect(statusOf(/^Sample size$/)).toBe("Different");
    expect(statusOf(/^Outcome measure$/)).toBe("Same");
    expect(statusOf(/^Baseline synthetic status/)).toBe("—");
    expect(statusOf(/^Synthetic dose/)).toBe("Same");
    expect(statusOf(/^Year$/)).toBe("—");
    expect(screen.getByRole("rowheader", { name: /^Baseline synthetic status/ }).textContent).toContain("Not evaluated");
    expect(screen.getByRole("rowheader", { name: /^Synthetic dose/ }).textContent).not.toContain("Not evaluated");
    expect(screen.getAllByText("50 mg", { selector: "td" })).toHaveLength(2);
  });

  it("marks a likely reason whose values differ, and a differing measure that maps to one outcome", () => {
    const data = copy();
    for (const node of data.graph.nodes) {
      if (node.kind === "finding" && node.id === "999000102-1") {
        const finding: FindingNode = node;
        finding.population = "Synthetic adults over 50";
        finding.outcome_reported = "synthetic mood item";
        finding.dimensions = { ...finding.dimensions, synthetic_dose: 5 };
      }
    }
    render(<PairPage data={data} pairId={POPULATION} />);
    expect(statusOf(/^Population$/)).toBe("Different · likely reason");
    expect(statusOf(/^Outcome measure$/)).toBe("Different · both map to synthetic symptoms");
    expect(statusOf(/^Synthetic dose/)).toBe("Different");
  });

  it("links back to the outcome and on to the next pair with the same reason", () => {
    const data = copy();
    const base = data.graph.edges.find((e): e is ContradictionEdge => e.kind === "contradiction" && e.pair_id === POPULATION);
    if (base === undefined) throw new Error("the fixture has the pair");
    data.graph.edges.push({ ...base, pair_id: `${POPULATION}|copy`, design_weight: 1 });
    render(<PairPage data={data} pairId={POPULATION} />);
    const back = href({ page: "question", outcome: "Synthetic symptoms" });
    expect(screen.getByRole("link", { name: "Back to all contradictions" }).getAttribute("href")).toBe(back);
    expect(screen.getByRole("link", { name: "Back to the findings" }).getAttribute("href")).toBe(back);
    expect(screen.getByRole("link", { name: "Next population pair · 2 of 2" }).getAttribute("href")).toBe(
      href({ page: "pair", pairId: `${POPULATION}|copy` }),
    );
    expect(screen.getByText("Research navigation demo, not medical advice.")).toBeTruthy();
  });

  it("a lone pair of its reason has no next link", () => {
    render(<PairPage data={fixtures.synthetic} pairId={POPULATION} />);
    expect(screen.queryByRole("link", { name: /^Next/ })).toBeNull();
  });

  it("names designs in the title when the two differ", () => {
    render(<PairPage data={fixtures.synthetic} pairId="Synthetic symptoms|999000102|999000103" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain(
      "One cohort study found a benefit. One trial found no effect.",
    );
  });

  it("not-found state: an unknown pair id says so and links back to the page", () => {
    render(<PairPage data={fixtures.synthetic} pairId="Synthetic symptoms|999000000|999000001" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("No such pair");
    expect(screen.getByRole("link", { name: "Back to all contradictions" }).getAttribute("href")).toBe("#/");
  });

  it("not-found state: an apparent pair has no pair page", () => {
    render(<PairPage data={fixtures.synthetic} pairId="Synthetic symptoms|999000102|999000106" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("No such pair");
  });
});
