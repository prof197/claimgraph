import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtures } from "../../fixtures";
import { href } from "../../routes";
import type { ContradictionEdge, Export, Outcome } from "../../types/export";
import { ContradictionPanel, FIRST_PAIRS, withoutReason } from "./ContradictionPanel";

function outcomeOf(data: Export, name: string): Outcome {
  const o = data.outcomes.find((x) => x.name === name);
  if (o === undefined) throw new Error(`no outcome ${name}`);
  return o;
}

function copy(data: Export): Export {
  return JSON.parse(JSON.stringify(data)) as Export;
}

/** The synthetic fixture with extra closely matched pairs, copies of its first one. */
function withMatchedPairs(extra: number): Export {
  const data = copy(fixtures.synthetic);
  const base = data.graph.edges.find(
    (e): e is ContradictionEdge => e.kind === "contradiction" && e.closely_matched === true,
  );
  if (base === undefined) throw new Error("the fixture has a closely matched pair");
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

// jsdom drops the space before the hidden study names that a browser keeps.
function pairLinks() {
  return within(panel()).queryAllByRole("link", { name: /^Compare in full\s*for / });
}

describe("the contradiction panel", () => {
  it("shows only the closely matched pairs, and says what closely matched means", () => {
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);

    expect(screen.getByText("3 closely matched pairs of studies on synthetic symptoms found different things.")).toBeTruthy();
    expect(
      screen.getByText(
        "Closely matched means alike in all 5 ways the comparison checks, each reported by both " +
          "studies: the same design, the same outcome measure, the same baseline synthetic status, " +
          "the same synthetic dose and sample sizes within 3× of each other. Dose, duration and " +
          "who was studied can still differ.",
      ),
    ).toBeTruthy();
    expect(pairLinks().map((a) => a.getAttribute("href"))).toEqual([
      href({ page: "pair", pairId: "Synthetic symptoms|999000102|999000104" }),
      href({ page: "pair", pairId: "Synthetic symptoms|999000101|999000102" }),
      href({ page: "pair", pairId: "Synthetic symptoms|999000104|999000105" }),
    ]);
    // Neither reason headings nor filters, and no count of candidate pairs.
    expect(within(panel()).queryAllByRole("heading", { level: 3 })).toHaveLength(0);
    expect(screen.queryByRole("group", { name: "Filter by likely reason" })).toBeNull();
    expect(panel().textContent).not.toMatch(/candidate/);
  });

  it("states the measured accuracy once, for the whole panel", () => {
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    expect(
      screen.getAllByText(/Checked against hand-labeled pairs, the verdict is right about 90% of the time and the reason about 50%\./),
    ).toHaveLength(1);
  });

  it("says so when the accuracy was not measured", () => {
    const data = copy(synthetic);
    data.method.evals = data.method.evals.filter((e) => e.eval !== "classification");
    render(<ContradictionPanel data={data} outcome={outcomeOf(data, "Synthetic symptoms")} />);
    expect(screen.getByText(/Its accuracy has not been measured for this page\./)).toBeTruthy();
  });

  it("sets the two studies side by side from their extracted fields, then the likely reason", () => {
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    const item = pairLinks()[1]?.closest("li");
    if (!item) throw new Error("the pair is a list item");

    expect(within(item).getByText("Found a benefit")).toBeTruthy();
    expect(within(item).getByText("No clear difference")).toBeTruthy();
    expect(within(item).getByText("Synthetic101 et al. 2011")).toBeTruthy();
    expect(within(item).getByText("Synthetic102 et al. 2012")).toBeTruthy();
    const terms = within(item).getAllByRole("term").map((t) => t.textContent);
    expect(terms).toEqual([
      "Population",
      "Intervention",
      "Comparator",
      "Sample size",
      "Population",
      "Intervention",
      "Comparator",
      "Sample size",
    ]);
    expect(within(item).getAllByText("Synthetic supplement X, 50 mg daily for 8 weeks")).toHaveLength(2);
    expect(within(item).getByText("18,000")).toBeTruthy();
    expect(within(item).getByText("Synthetic sentence 1 from synthetic abstract 999000101.")).toBeTruthy();
    expect(item.textContent).toContain("Likely reason: population. synthetic: depressed adults vs a general older population");
  });

  it("sets the columns in the classifier's order, and words an observational study's badge as a link", () => {
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    const item = pairLinks()[2]?.closest("li");
    if (!item) throw new Error("the pair is a list item");
    // Pair 104|105: study a, the trial, on the left; b, the cross-sectional study, on the right.
    const names = [...item.querySelectorAll("span")].map((e) => e.textContent).filter((t) => /^Synthetic10\d et al\./.test(t ?? ""));
    expect(names).toEqual(["Synthetic104 et al. 2014", "Synthetic105 et al. 2015"]);
    expect(within(item).getByText("Found a benefit")).toBeTruthy();
    expect(within(item).getByText("Linked to worse synthetic mood")).toBeTruthy();
  });

  it("names one unclassified pair in the singular", () => {
    const outcome = { ...outcomeOf(synthetic, "Synthetic cases"), candidate_pairs: 1 };
    render(<ContradictionPanel data={synthetic} outcome={outcome} />);
    expect(screen.getByText("Its pair of studies has not been classified yet.")).toBeTruthy();
  });

  it("more closely matched pairs than fit: shows the first ones, then all on request", async () => {
    const user = userEvent.setup();
    const data = withMatchedPairs(FIRST_PAIRS);
    render(<ContradictionPanel data={data} outcome={outcomeOf(data, "Synthetic symptoms")} />);
    expect(pairLinks()).toHaveLength(FIRST_PAIRS);

    await user.click(screen.getByRole("button", { name: `Show all ${FIRST_PAIRS + 3} pairs` }));

    expect(pairLinks()).toHaveLength(FIRST_PAIRS + 3);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });

  it("closely matched apparent pairs: listed collapsed beneath, side by side", async () => {
    const user = userEvent.setup();
    const { container } = render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    const details = container.querySelector("details");
    expect(details?.open).toBe(false);

    await user.click(screen.getByText("1 closely matched pair only looked contradictory"));

    expect(details?.open).toBe(true);
    expect(details?.textContent).toContain("Why not: design or risk of bias. underpowered: a harm seen in 18 people");
    expect(details?.textContent).toContain("Synthetic106 et al. 2016");
  });

  it("no closely matched pair disagrees, but one only looked like it", () => {
    render(<ContradictionPanel data={synthetic} outcome={outcomeOf(synthetic, "Synthetic remission")} />);
    expect(screen.getByText("No closely matched studies on synthetic remission disagree.")).toBeTruthy();
    expect(pairLinks()).toHaveLength(0);
    expect(screen.getByText("1 closely matched pair only looked contradictory")).toBeTruthy();
  });

  it("none classified yet: says the pairs wait", () => {
    render(<ContradictionPanel data={synthetic} outcome={outcomeOf(synthetic, "Synthetic cases")} />);
    expect(screen.getByText("No closely matched studies on synthetic cases disagree.")).toBeTruthy();
    expect(screen.getByText("Its pairs of studies have not been classified yet.")).toBeTruthy();
    expect(screen.queryByText(/looked contradictory/)).toBeNull();
    expect(screen.queryByText(/Closely matched means/)).toBeNull();
  });

  it("nothing to compare: a thin outcome whose studies all agree", () => {
    const data = fixtures.minimal;
    render(<ContradictionPanel data={data} outcome={outcomeOf(data, "Synthetic symptoms")} />);
    expect(screen.getByText("No two studies on it point different ways, so there was nothing to compare.")).toBeTruthy();
  });

  it("keyboard: tabbing reaches every pair", async () => {
    const user = userEvent.setup();
    render(<ContradictionPanel data={synthetic} outcome={symptoms} />);
    const reached: string[] = [];
    for (let i = 0; i < 10; i += 1) {
      await user.tab();
      const el = document.activeElement;
      if (el instanceof HTMLAnchorElement) reached.push(el.getAttribute("href") ?? "");
    }
    expect(new Set(reached).size).toBe(3);
  });

  it("drops a detail's opening reason name, which the line has just said", () => {
    expect(withoutReason("population: inpatients vs older adults", "population")).toBe("inpatients vs older adults");
    expect(withoutReason("Outcome measure: CES-D vs PHQ-9", "outcome_measure")).toBe("CES-D vs PHQ-9");
    expect(withoutReason("design: cross-sectional vs trial", "design_or_bias")).toBe("design: cross-sectional vs trial");
  });
});
