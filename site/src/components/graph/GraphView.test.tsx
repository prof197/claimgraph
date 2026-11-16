import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { fixtures } from "../../fixtures";
import type { Export, Outcome } from "../../types/export";
import { denseExport } from "./dense.testdata";
import { EDGE_LIMIT, GraphView } from "./GraphView";

function outcomeOf(data: Export, name: string): Outcome {
  const o = data.outcomes.find((x) => x.name === name);
  if (o === undefined) throw new Error(`no outcome ${name}`);
  return o;
}

function Harness({ data, outcome, initial = null }: { data: Export; outcome: Outcome; initial?: string | null }) {
  const [selected, setSelected] = useState<string | null>(initial);
  return (
    <>
      <GraphView data={data} outcome={outcome} selected={selected} onSelect={setSelected} />
      <output>{selected ?? "none"}</output>
    </>
  );
}

function graph() {
  return screen.getByRole("group", { name: /as a graph/ });
}

const symptoms = outcomeOf(fixtures.synthetic, "Synthetic symptoms");

describe("the graph view", () => {
  it("draws a real button for each study, naming its direction in words", () => {
    render(<Harness data={fixtures.synthetic} outcome={symptoms} />);
    const dots = within(graph()).getAllByRole("button");
    expect(dots).toHaveLength(8);
    expect(within(graph()).getByRole("button", { name: /^Synthetic101 et al. 2011: Benefit, randomized controlled trial, n 120, 2 contradictions$/ })).toBeTruthy();
    expect(within(graph()).getByRole("button", { name: /^Synthetic108 et al. 2018: .*subgroup results only, not counted/ })).toBeTruthy();
    expect(screen.getByText(/arranged around the outcome: 4 benefit on the left, 2 no effect on the right, 2 harm at the bottom/)).toBeTruthy();
  });

  it("selects a study on click and calls onSelect with its finding", async () => {
    const user = userEvent.setup();
    render(<Harness data={fixtures.synthetic} outcome={symptoms} />);
    await user.click(within(graph()).getByRole("button", { name: /^Synthetic102 et al. 2012/ }));
    expect(screen.getByRole("status").textContent).toBe("999000102-1");
    expect(within(graph()).getByRole("button", { name: /^Synthetic102 et al. 2012/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("selected state: rings the study and draws all of its contradictions solid", () => {
    const { container } = render(<Harness data={fixtures.synthetic} outcome={symptoms} initial="999000102-1" />);
    expect(container.querySelector("[data-selected]")?.getAttribute("data-selected")).toBe("999000102-1");
    const strong = [...container.querySelectorAll("path[data-pair]")].filter((p) => p.getAttribute("class")?.includes("edgeStrong"));
    expect(strong.map((p) => p.getAttribute("data-pair")).sort()).toEqual([
      "Synthetic symptoms|999000101|999000102",
      "Synthetic symptoms|999000102|999000103",
      "Synthetic symptoms|999000102|999000104",
    ]);
  });

  it("selected state: a subgroup finding chosen elsewhere marks its study's dot", () => {
    render(<Harness data={fixtures.synthetic} outcome={symptoms} initial="999000101-2" />);
    expect(within(graph()).getByRole("button", { name: /^Synthetic101 et al. 2011/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("keyboard: the graph is one tab stop, arrow keys move between dots and Enter selects", async () => {
    const user = userEvent.setup();
    render(<Harness data={fixtures.synthetic} outcome={symptoms} />);
    const focusable = within(graph())
      .getAllByRole("button")
      .filter((b) => b.tabIndex === 0);
    expect(focusable).toHaveLength(1);

    await user.tab();
    const first = document.activeElement;
    expect(first?.getAttribute("aria-label")).toMatch(/Benefit/);
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).not.toBe(first);
    await user.keyboard("{End}");
    expect(document.activeElement?.getAttribute("aria-label")).toMatch(/Harm/);
    await user.keyboard("{Enter}");
    expect(screen.getByRole("status").textContent).toMatch(/^99900010[56]-1$/);
    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(first);
  });

  it("thin state: names every dot when there are only a few", () => {
    const data = fixtures.minimal;
    const { container } = render(<Harness data={data} outcome={outcomeOf(data, "Synthetic symptoms")} />);
    const labels = [...container.querySelectorAll("text")].map((t) => t.textContent);
    expect(labels).toContain("Synthetic301 et al. 2021");
    expect(labels).toContain("Synthetic302 et al. 2021");
    expect(labels).toContain("Benefit · 2");
    // No contradictions, so no contradiction key.
    expect(within(screen.getByRole("list", { name: "Legend" })).queryByText("Contradiction")).toBeNull();
  });

  it("empty state: an outcome with no findings says so instead of drawing", () => {
    const data = JSON.parse(JSON.stringify(fixtures.synthetic)) as Export;
    data.graph.nodes = data.graph.nodes.filter((n) => n.outcome !== "Synthetic cases");
    render(<Harness data={data} outcome={outcomeOf(data, "Synthetic cases")} />);
    expect(screen.getByText("No findings on synthetic cases to draw.")).toBeTruthy();
    expect(screen.queryByRole("group", { name: /as a graph/ })).toBeNull();
  });

  it("crowded state: draws only the strongest contradictions and says so", () => {
    const data = denseExport(120, 90);
    const { container } = render(<Harness data={data} outcome={outcomeOf(data, "Synthetic symptoms")} />);
    expect(container.querySelectorAll("path[data-pair]")).toHaveLength(EDGE_LIMIT);
    expect(screen.getByText(/Lines show the 12 strongest of 96 contradictions/)).toBeTruthy();
    // With this many studies, dots are not named until selected.
    expect([...container.querySelectorAll("text")].map((t) => t.textContent)).not.toContain("Synthetic0 2000");
  });

  it("is deterministic in the page too: two renders draw the same circles", () => {
    const draw = () => {
      const { container, unmount } = render(<Harness data={fixtures.synthetic} outcome={symptoms} />);
      const circles = [...container.querySelectorAll("circle[data-node]")].map((c) =>
        ["data-node", "cx", "cy", "r"].map((a) => c.getAttribute(a)).join(" "),
      );
      unmount();
      return circles;
    };
    expect(draw()).toEqual(draw());
  });
});

it("gives each sector the counted studies and marks the rest not counted", () => {
  const data = fixtures.synthetic;
  const outcome = data.outcomes.find((o) => o.name === "Synthetic symptoms");
  if (outcome === undefined) throw new Error("the synthetic fixture has symptoms");
  const { container } = render(
    <GraphView data={data} outcome={outcome} selected={null} onSelect={() => {}} />,
  );
  const labels = [...container.querySelectorAll("text")].map((t) => t.textContent);

  // The strip counts 3 benefit, 1 no effect and 2 harm; one benefit study has only subgroup
  // results and one no-effect study has a design without a weight.
  expect(labels).toContain("Benefit · 3 · 1 not counted");
  expect(labels).toContain("No effect · 1 · 1 not counted");
  expect(labels).toContain("Harm · 2");
});
