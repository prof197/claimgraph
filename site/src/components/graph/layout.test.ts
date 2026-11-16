import { fixtures } from "../../fixtures";
import type { Export, Outcome } from "../../types/export";
import { denseExport } from "./dense.testdata";
import { CX, CY, HEIGHT, HUB_R, WIDTH, hubLines, keyboardOrder, layoutGraph } from "./layout";

function outcome(data: Export, index = 0): Outcome {
  const o = data.outcomes[index];
  if (o === undefined) throw new Error("no such outcome");
  return o;
}

function coordinates(data: Export, o: Outcome) {
  const layout = layoutGraph(data, o);
  return {
    nodes: Object.fromEntries(layout.nodes.map((n) => [n.id, [n.x, n.y, n.r]])),
    edges: Object.fromEntries(layout.edges.map((e) => [e.pairId, e.d])),
  };
}

describe("the graph layout", () => {
  it("is deterministic: the same export gives the same coordinates", () => {
    const data = fixtures.synthetic;
    const first = coordinates(data, outcome(data));
    const second = coordinates(JSON.parse(JSON.stringify(data)) as Export, outcome(data));
    expect(second).toEqual(first);
    // Pinned, so a change to the geometry is a visible, deliberate change.
    expect(first.nodes["999000102-1"]).toEqual([488.2, 199.5, 15]);
  });

  it("does not depend on the order of the export's arrays", () => {
    const data = denseExport(60, 30);
    const shuffled = JSON.parse(JSON.stringify(data)) as Export;
    shuffled.studies.reverse();
    shuffled.graph.nodes.reverse();
    shuffled.graph.edges.reverse();
    expect(coordinates(shuffled, outcome(shuffled))).toEqual(coordinates(data, outcome(data)));
  });

  it("puts benefit left of the hub, no effect right and harm below", () => {
    const data = denseExport(48);
    for (const node of layoutGraph(data, outcome(data)).nodes) {
      if (node.direction === "benefit") expect(node.x).toBeLessThan(CX);
      if (node.direction === "none") expect(node.x).toBeGreaterThan(CX);
      if (node.direction === "harm") expect(node.y).toBeGreaterThan(CY);
    }
  });

  it.each([6, 48, 320])("keeps %i studies apart, off the hub and inside the drawing", (n) => {
    const data = denseExport(n);
    const nodes = layoutGraph(data, outcome(data)).nodes;
    for (const [i, a] of nodes.entries()) {
      expect(Math.hypot(a.x - CX, a.y - CY)).toBeGreaterThan(HUB_R + a.r);
      expect(a.x - a.r).toBeGreaterThanOrEqual(0);
      expect(a.x + a.r).toBeLessThanOrEqual(WIDTH);
      expect(a.y - a.r).toBeGreaterThanOrEqual(0);
      expect(a.y + a.r).toBeLessThanOrEqual(HEIGHT);
      for (const b of nodes.slice(i + 1)) {
        // Coordinates are rounded to a tenth, so allow that much.
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(a.r + b.r - 0.2);
      }
    }
  });

  it("draws one dot per study: its headline finding, else its first subgroup finding", () => {
    const data = fixtures.synthetic;
    const layout = layoutGraph(data, outcome(data));
    expect(layout.nodes.map((n) => n.id).sort()).toEqual([
      "999000101-1",
      "999000102-1",
      "999000103-1",
      "999000104-1",
      "999000105-1",
      "999000106-1",
      "999000107-1",
      "999000108-1",
    ]);
    const subgroupOnly = layout.byStudy.get("999000108");
    expect(subgroupOnly?.headline).toBe(false);
    expect(subgroupOnly?.spoke).toBeNull();
    expect(layout.byStudy.get("999000101")?.spoke).not.toBeNull();
  });

  it("fills randomized trials and sizes dots by sample size", () => {
    const data = fixtures.synthetic;
    const nodes = new Map(layoutGraph(data, outcome(data)).nodes.map((n) => [n.study, n]));
    expect(nodes.get("999000101")?.randomized).toBe(true);
    expect(nodes.get("999000103")?.randomized).toBe(false);
    // 18,000 people outweigh 120; no sample size draws the smallest dot.
    expect(nodes.get("999000102")?.r).toBeGreaterThan(nodes.get("999000101")?.r ?? Infinity);
    expect(nodes.get("999000107")?.r).toBe(4);
  });

  it("links exactly the outcome's contradictions, strongest first", () => {
    const data = fixtures.synthetic;
    const edges = layoutGraph(data, outcome(data)).edges;
    expect(edges.map((e) => e.pairId)).toEqual([
      "Synthetic symptoms|999000102|999000104",
      "Synthetic symptoms|999000101|999000102",
      "Synthetic symptoms|999000102|999000103",
      "Synthetic symptoms|999000104|999000105",
      "Synthetic symptoms|999000101|999000105",
      "Synthetic symptoms|999000103|999000105",
    ]);
    expect(layoutGraph(data, outcome(data, 2)).edges).toEqual([]);
  });

  it("orders the keyboard sector by sector", () => {
    const data = fixtures.synthetic;
    const order = keyboardOrder(layoutGraph(data, outcome(data)).nodes).map((n) => n.direction);
    expect(order).toEqual([...order].sort((a, b) => ["benefit", "none", "harm"].indexOf(a) - ["benefit", "none", "harm"].indexOf(b)));
  });

  it("breaks the hub's name into short lines", () => {
    expect(hubLines("Depressive symptoms")).toEqual(["Depressive", "symptoms"]);
    expect(hubLines("Remission")).toEqual(["Remission"]);
    expect(hubLines("Quality of life in very old adults").length).toBe(3);
  });
});
