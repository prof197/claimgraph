// Ordering of an outcome's contradiction pairs, shared by the panel and the pair page.
import { contradictionsOn, findingsOn, studiesByKey } from "../../lib/data";
import type {
  ApparentPair,
  ContradictionEdge,
  Export,
  FindingNode,
  Outcome,
} from "../../types/export";

/** Stronger designs first, then larger combined samples (unknown last), then pair id, so
 * the order never depends on the export's. */
export function comparePairs(
  a: { weight: number; n: number | null; id: string },
  b: { weight: number; n: number | null; id: string },
): number {
  if (a.weight !== b.weight) {
    return b.weight - a.weight;
  }
  if (a.n !== b.n) {
    if (a.n === null) return 1;
    if (b.n === null) return -1;
    return b.n - a.n;
  }
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

function edgeKey(edge: ContradictionEdge) {
  return { weight: edge.design_weight, n: edge.sample_size, id: edge.pair_id };
}

export function orderedPairs(data: Export, outcome: Outcome): ContradictionEdge[] {
  return contradictionsOn(data, outcome).sort((a, b) => comparePairs(edgeKey(a), edgeKey(b)));
}

/** Apparent pairs carry no combined weight or size, so they are summed from the studies. */
export function orderedApparent(data: Export, outcome: Outcome): ApparentPair[] {
  const studies = studiesByKey(data);
  const key = (pair: ApparentPair) => {
    const a = studies.get(pair.study_a);
    const b = studies.get(pair.study_b);
    const sizes = [a?.sample_size ?? null, b?.sample_size ?? null];
    return {
      weight: (a?.weight ?? 0) + (b?.weight ?? 0),
      n: sizes.includes(null) ? null : (sizes[0] ?? 0) + (sizes[1] ?? 0),
      id: pair.pair_id,
    };
  };
  return [...outcome.apparent].sort((a, b) => comparePairs(key(a), key(b)));
}

/** Findings on an outcome by id, for looking up both ends of many pairs at once. */
export function findingsById(data: Export, outcome: Outcome): Map<string, FindingNode> {
  return new Map(findingsOn(data, outcome).map((f) => [f.id, f]));
}

/** Both findings of a pair in the classifier's order, study a then study b, so its detail's
 * "A" and "B" read left to right. */
export function sidesOf(
  byId: Map<string, FindingNode>,
  edge: Pick<ContradictionEdge, "source" | "target">,
): [FindingNode, FindingNode] | undefined {
  const a = byId.get(edge.source);
  const b = byId.get(edge.target);
  return a === undefined || b === undefined ? undefined : [a, b];
}
