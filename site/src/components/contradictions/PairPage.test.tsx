import { render, screen, within } from "@testing-library/react";
import { fixtures } from "../../fixtures";
import { href } from "../../routes";
import type { Export, FindingNode } from "../../types/export";
import { PairPage } from "./PairPage";

const POPULATION = "Synthetic symptoms|999000101|999000102";

/** The verdict, then the smaller note beneath it when there is one: "Different | likely reason". */
function statusOf(rowName: RegExp): string {
  const cell = screen.getByRole("rowheader", { name: rowName }).closest("tr")?.querySelector("td:last-child");
  return Array.from(cell?.children ?? [], (c) => c.textContent).join(" | ");
}

function copy(): Export {
  return JSON.parse(JSON.stringify(fixtures.synthetic)) as Export;
}

describe("the pair page", () => {
  it("shows both findings side by side, in the classifier's order, with the likely reason", () => {
    render(<PairPage data={fixtures.synthetic} pairId={POPULATION} />);
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toBe(
      "Contradiction · Synthetic symptomsOne trial: found a benefit. One trial: no clear difference.",
    );
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
    expect(statusOf(/^Population$/)).toBe("Same | named as the likely reason");
    expect(statusOf(/^Intervention$/)).toBe("Different | also differs");
    expect(statusOf(/^Comparator$/)).toBe("Same");
    expect(statusOf(/^Design or risk of bias$/)).toBe("Same");
    expect(statusOf(/^Sample size$/)).toBe("Different");
    expect(statusOf(/^Outcome measure$/)).toBe("Same");
    expect(statusOf(/^Baseline synthetic status/)).toBe("—");
    expect(statusOf(/^Synthetic dose/)).toBe("Same");
    expect(statusOf(/^Year$/)).toBe("—");
    expect(screen.getByRole("rowheader", { name: /^Baseline synthetic status/ }).textContent).toContain("Not evaluated");
    expect(screen.getByRole("rowheader", { name: /^Synthetic dose/ }).textContent).not.toContain("Not evaluated");
    // A dimension's short label is what shows; its full label is what a screen reader hears.
    const baseline = screen.getByRole("rowheader", { name: /^Baseline synthetic status/ });
    expect(baseline.querySelector('[aria-hidden="true"]')?.textContent).toBe("Baseline status");
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
    expect(statusOf(/^Population$/)).toBe("Different | likely reason");
    expect(statusOf(/^Outcome measure$/)).toBe("Different | both map to synthetic symptoms");
    expect(statusOf(/^Synthetic dose/)).toBe("Different");
  });

  it("links back to the outcome's contradictions and on to the next closely matched pair", () => {
    render(<PairPage data={fixtures.synthetic} pairId={POPULATION} />);
    // A reader comes from the panel, so both ways back, in the header and under the pair, land on it.
    const back = screen.getAllByRole("link", { name: "Back to where studies disagree" });
    expect(back.map((a) => a.getAttribute("href"))).toEqual([
      "/outcome/Synthetic%20symptoms#where-studies-disagree",
      "/outcome/Synthetic%20symptoms#where-studies-disagree",
    ]);
    // The panel's order: 102|104, then this pair, then 104|105.
    expect(screen.getByRole("link", { name: "Next closely matched pair · 3 of 3" }).getAttribute("href")).toBe(
      href({ page: "pair", pairId: "Synthetic symptoms|999000104|999000105" }),
    );
    expect(screen.getByText("Research navigation demo, not medical advice.")).toBeTruthy();
  });

  it("a pair the panel does not list has no next link", () => {
    render(<PairPage data={fixtures.synthetic} pairId="Synthetic symptoms|999000102|999000103" />);
    expect(screen.queryByRole("link", { name: /^Next/ })).toBeNull();
  });

  it("names each design in the title, in the classifier's order, with an observational study's link", () => {
    render(<PairPage data={fixtures.synthetic} pairId="Synthetic symptoms|999000102|999000103" />);
    // Study a, the trial that found no clear difference, comes first though b found an effect.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain(
      "One trial: no clear difference. One cohort study: linked to better synthetic mood.",
    );
    const cards = within(screen.getByRole("region", { name: "The two findings" })).getAllByRole("article");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Synthetic102 et al. 2012", "Synthetic103 et al. 2013"]);
  });

  it("shows a comparison across participants as having no comparison group", () => {
    const data = copy();
    for (const node of data.graph.nodes) {
      if (node.kind === "finding" && node.id === "999000102-1") node.comparator = "Across participants";
    }
    render(<PairPage data={data} pairId={POPULATION} />);
    expect(screen.getAllByText("No comparison group (association across participants)").length).toBeGreaterThan(0);
  });

  it("not-found state: an unknown pair id says so and links back to the page", () => {
    render(<PairPage data={fixtures.synthetic} pairId="Synthetic symptoms|999000000|999000001" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("No such pair");
    expect(screen.getByRole("link", { name: "Back to where studies disagree" }).getAttribute("href")).toBe("/");
  });

  it("not-found state: an apparent pair has no pair page", () => {
    render(<PairPage data={fixtures.synthetic} pairId="Synthetic symptoms|999000102|999000106" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("No such pair");
  });
});
