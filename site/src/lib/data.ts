// Lookups over one export that more than one part of the page needs.
import type { ContradictionEdge, Export, FindingNode, Outcome, Study } from "../types/export";

export function findingsOn(data: Export, outcome: Outcome): FindingNode[] {
  return data.graph.nodes.filter(
    (node): node is FindingNode => node.kind === "finding" && node.outcome === outcome.name,
  );
}

export function contradictionsOn(data: Export, outcome: Outcome): ContradictionEdge[] {
  const prefix = `${outcome.name}|`;
  return data.graph.edges.filter(
    (edge): edge is ContradictionEdge =>
      edge.kind === "contradiction" && edge.pair_id.startsWith(prefix),
  );
}

export function studiesByKey(data: Export): Map<string, Study> {
  return new Map(data.studies.map((study) => [study.key, study]));
}

export function outcomeNamed(data: Export, name: string | null): Outcome | undefined {
  return name === null ? data.outcomes[0] : data.outcomes.find((o) => o.name === name);
}
