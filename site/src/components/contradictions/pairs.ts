// Ordering and grouping of an outcome's contradiction pairs, shared by the panel and the pair page.
import { contradictionsOn, findingsOn, studiesByKey } from "../../lib/data";
import type {
  ApparentPair,
  ContradictionEdge,
  Direction,
  Export,
  FindingNode,
  Outcome,
  Reason,
} from "../../types/export";
import { REASONS } from "./labels";

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

export interface ReasonGroup {
  reason: Reason;
  pairs: ContradictionEdge[];
}

/** Non-empty groups in the fixed reason order, each keeping the pairs' order. */
export function groupByReason(pairs: ContradictionEdge[]): ReasonGroup[] {
  return REASONS.map((reason) => ({ reason, pairs: pairs.filter((p) => p.reason === reason) })).filter(
    (group) => group.pairs.length > 0,
  );
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

const SIDE_ORDER: Record<Direction, number> = { benefit: 0, harm: 1, none: 2 };

/** Findings on an outcome by id, for looking up both ends of many pairs at once. */
export function findingsById(data: Export, outcome: Outcome): Map<string, FindingNode> {
  return new Map(findingsOn(data, outcome).map((f) => [f.id, f]));
}

/** Both findings of a pair, the one that found an effect first, as the panel reads them. */
export function orient(
  byId: Map<string, FindingNode>,
  edge: ContradictionEdge,
): [FindingNode, FindingNode] | undefined {
  const source = byId.get(edge.source);
  const target = byId.get(edge.target);
  if (source === undefined || target === undefined) {
    return undefined;
  }
  return SIDE_ORDER[source.direction] <= SIDE_ORDER[target.direction]
    ? [source, target]
    : [target, source];
}
