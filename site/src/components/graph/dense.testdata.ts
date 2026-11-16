// A visibly synthetic export with many studies on one outcome, built from the synthetic fixture
// for tests of a crowded graph. Every number comes from the index, so it is the same each run.
import { fixtures } from "../../fixtures";
import type { ContradictionEdge, Design, Direction, Export, FindingNode, Study } from "../../types/export";

const DESIGNS: readonly Design[] = ["rct", "rct", "cohort", "cross_sectional", "rct", "case_series"];
const WEIGHT: Partial<Record<Design, number>> = { rct: 3, cohort: 2, cross_sectional: 1, case_series: 0.5 };

export function denseExport(studies: number, pairs = 0): Export {
  const data = JSON.parse(JSON.stringify(fixtures.synthetic)) as Export;
  const outcome = data.outcomes[0];
  if (outcome === undefined) throw new Error("the synthetic fixture has an outcome");
  const findings: FindingNode[] = [];
  for (let i = 0; i < studies; i += 1) {
    const key = String(999200000 + i);
    const design = DESIGNS[i % DESIGNS.length] ?? "rct";
    // Roughly half benefit, a third no effect, the rest harm.
    const direction: Direction = i % 6 < 3 ? "benefit" : i % 6 < 5 ? "none" : "harm";
    const study: Study = {
      key,
      pmids: [key],
      title: `Synthetic dense study ${i}`,
      first_author: `Synthetic${i}`,
      journal: "Synthetic Journal",
      year: 2000 + (i % 25),
      design,
      weight: WEIGHT[design] ?? null,
      sample_size: i % 7 === 0 ? null : 20 + ((i * 7919) % 5000),
      trial_ids: [],
    };
    data.studies.push(study);
    const finding: FindingNode = {
      kind: "finding",
      id: `${key}-1`,
      outcome: outcome.name,
      study: key,
      pmid: key,
      direction,
      headline: true,
      claim: `Synthetic claim ${i}.`,
      quote: `Synthetic sentence from synthetic abstract ${key}.`,
      outcome_reported: "synthetic score",
      is_primary: true,
      subgroup: null,
      population: "Synthetic adults",
      intervention: "Synthetic supplement X",
      comparator: "Synthetic placebo",
      dimensions: {},
    };
    findings.push(finding);
    data.graph.nodes.push(finding);
    data.graph.edges.push({ kind: "outcome", source: finding.id, target: outcome.hub });
  }
  const effects = findings.filter((f) => f.direction !== "none");
  const nulls = findings.filter((f) => f.direction === "none");
  for (let i = 0; i < pairs && effects.length > 0 && nulls.length > 0; i += 1) {
    const a = effects[i % effects.length];
    const b = nulls[(i * 3) % nulls.length];
    if (a === undefined || b === undefined) break;
    const edge: ContradictionEdge = {
      kind: "contradiction",
      source: a.id,
      target: b.id,
      pair_id: `${outcome.name}|${a.study}|${b.study}|${i}`,
      pair_kind: "effect_vs_none",
      reason: "population",
      also_differs: [],
      detail: `synthetic: dense pair ${i}`,
      rationale: "Synthetic rationale.",
      design_weight: 6 - (i % 4),
      sample_size: i % 5 === 0 ? null : 1000 + i,
    };
    data.graph.edges.push(edge);
  }
  return data;
}
