import { fixtures } from "../../fixtures";
import type { Outcome } from "../../types/export";
import { comparePairs, findingsById, orderedApparent, orderedPairs, sidesOf } from "./pairs";

const data = fixtures.synthetic;

function outcome(name: string): Outcome {
  const o = data.outcomes.find((x) => x.name === name);
  if (o === undefined) throw new Error(`no outcome ${name}`);
  return o;
}

describe("pair order", () => {
  it("puts the stronger combined design first, then the larger combined sample, then the id", () => {
    expect(orderedPairs(data, outcome("Synthetic symptoms")).map((p) => p.pair_id)).toEqual([
      "Synthetic symptoms|999000102|999000104",
      "Synthetic symptoms|999000101|999000102",
      "Synthetic symptoms|999000102|999000103",
      "Synthetic symptoms|999000104|999000105",
      "Synthetic symptoms|999000101|999000105",
      "Synthetic symptoms|999000103|999000105",
    ]);
  });

  it("puts an unknown combined sample size last, and breaks full ties by pair id", () => {
    const keys = [
      { weight: 6, n: null, id: "a" },
      { weight: 6, n: 10, id: "b" },
      { weight: 6, n: 10, id: "a" },
      { weight: 7, n: null, id: "z" },
    ];
    expect(keys.sort(comparePairs).map((k) => `${k.weight}/${k.n}/${k.id}`)).toEqual([
      "7/null/z",
      "6/10/a",
      "6/10/b",
      "6/null/a",
    ]);
  });

  it("orders apparent pairs by their studies' summed weight and size", () => {
    expect(orderedApparent(data, outcome("Synthetic remission")).map((p) => p.pair_id)).toEqual([
      "Synthetic remission|999000102|999000110",
      "Synthetic remission|999000101|999000111",
    ]);
  });

  it("reads a pair in the classifier's order, study a then study b", () => {
    const o = outcome("Synthetic symptoms");
    const byId = findingsById(data, o);
    const [pair] = orderedPairs(data, o);
    if (pair === undefined) throw new Error("the fixture has pairs");
    expect(sidesOf(byId, pair)?.map((f) => f.id)).toEqual([pair.source, pair.target]);
    const reversed = { ...pair, source: pair.target, target: pair.source };
    expect(sidesOf(byId, reversed)?.map((f) => f.id)).toEqual([pair.target, pair.source]);
  });
});
