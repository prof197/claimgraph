// Generated from schema/export.schema.json by `npm run types`; do not edit by hand.

/**
 * Why two studies disagree: Cochrane's clinical and methodological heterogeneity.
 */
export type Reason =
  "population" | "intervention" | "comparator" | "outcome_measure" | "design_or_bias" | "unclear";
export type Design =
  | "rct"
  | "non_randomized_trial"
  | "cohort"
  | "case_control"
  | "cross_sectional"
  | "case_series"
  | "systematic_review"
  | "meta_analysis"
  | "narrative_review"
  | "case_report"
  | "animal"
  | "in_vitro"
  | "other";
export type Weighting = "design" | "unweighted" | "sample_size";
export type Direction = "benefit" | "harm" | "none";
export type PairKind = "effect_vs_none" | "benefit_vs_harm";
/**
 * Classification arms; the values double as `--depth` names.
 */
export type Arm =
  "single-small" | "single-mid" | "self-consistency" | "self-moa" | "council" | "council-ranked";

export interface Export {
  schema_version: 1;
  question: Question;
  outcomes: Outcome[];
  studies: Study[];
  graph: Graph;
  reviews: Review[];
  method: Method;
}
export interface Question {
  id: string;
  title: string;
  retrieved: string;
  abstracts: number;
  mapping_reviewed: boolean;
  dimensions: Dimension[];
  found: FoundWords;
}
/**
 * A custom dimension from the question config, as the finding card shows it.
 */
export interface Dimension {
  name: string;
  label: string;
  short_label?: string | null;
  refines: Reason;
  unit: string | null;
  evaluated: boolean;
}
/**
 * A study's badge, worded by design: a trial gave the exposure and found a difference or
 * not; an observational study found a link, which is not a benefit. Each question words its
 * own, so its outcome can be named.
 */
export interface FoundWords {
  trial: DirectionWords;
  observational: DirectionWords;
}
/**
 * What a study's badge says for each direction it found.
 */
export interface DirectionWords {
  benefit: string;
  none: string;
  harm: string;
}
export interface Outcome {
  name: string;
  hub: string;
  higher_is_better: boolean;
  summary: Summary;
  candidate_pairs: number;
  classified_pairs: number;
  apparent: ApparentPair[];
}
/**
 * The summary strip for one outcome. Studies, not findings, are the unit.
 */
export interface Summary {
  studies: number;
  counts: DirectionCounts;
  designs: DesignCount[];
  thin: boolean;
  views: WeightedView[];
  subgroup_only: number;
  uncounted: number;
}
export interface DirectionCounts {
  benefit: number;
  none: number;
  harm: number;
}
export interface DesignCount {
  design: Design;
  studies: number;
}
export interface WeightedView {
  weighting: Weighting;
  studies: number;
  shares: DirectionShares;
  leading: Direction | null;
  changes_leading: boolean;
}
export interface DirectionShares {
  benefit: number;
  none: number;
  harm: number;
}
/**
 * A pair that looked contradictory but is not, with the one line that says why.
 */
export interface ApparentPair {
  pair_id: string;
  pair_kind: PairKind;
  study_a: string;
  study_b: string;
  finding_a: string;
  finding_b: string;
  reason: Reason;
  detail: string;
  closely_matched?: boolean;
}
/**
 * A primary study: one paper, or the papers of one trial, the earliest leading.
 */
export interface Study {
  key: string;
  /**
   * @minItems 1
   */
  pmids: [string, ...string[]];
  title: string;
  first_author: string | null;
  journal: string;
  year: number;
  design: Design;
  weight: number | null;
  sample_size: number | null;
  trial_ids: string[];
  trial: boolean;
}
/**
 * NetworkX node-link data: `nx.node_link_data(graph, edges="edges")`.
 */
export interface Graph {
  directed: false;
  multigraph: false;
  graph: {
    [k: string]: string;
  };
  nodes: (HubNode | FindingNode)[];
  edges: (OutcomeEdge | ContradictionEdge)[];
}
export interface HubNode {
  kind: "hub";
  id: string;
  outcome: string;
}
/**
 * One finding, with every field the finding card shows.
 */
export interface FindingNode {
  kind: "finding";
  id: string;
  outcome: string;
  study: string;
  pmid: string;
  direction: Direction;
  headline: boolean;
  claim: string;
  quote: string;
  outcome_reported: string;
  is_primary: boolean;
  subgroup: string | null;
  population: string;
  intervention: string;
  comparator: string;
  dimensions: {
    [k: string]: string | number | null;
  };
}
export interface OutcomeEdge {
  kind: "outcome";
  source: string;
  target: string;
}
/**
 * A genuine contradiction between two studies' headline findings.
 */
export interface ContradictionEdge {
  kind: "contradiction";
  source: string;
  target: string;
  pair_id: string;
  pair_kind: PairKind;
  reason: Reason;
  also_differs: Reason[];
  detail: string;
  rationale: string;
  design_weight: number;
  sample_size: number | null;
  closely_matched?: boolean;
}
/**
 * Listed for context and never counted: reviews pool studies already on the page.
 */
export interface Review {
  pmid: string;
  title: string;
  first_author: string | null;
  journal: string;
  year: number;
  design: Design;
  summary: string | null;
}
export interface Method {
  records_found: number;
  abstracts_kept: number;
  findings: number;
  studies: number;
  drops: Drop[];
  candidate_pairs: number;
  classified_pairs: number;
  depth: Arm | null;
  selection?: PairSelection | null;
  costs: StageCost[];
  evals: EvalResult[];
}
/**
 * Something the pipeline left out, and how many, for the method section.
 */
export interface Drop {
  what: string;
  count: number;
}
/**
 * How the classified pairs were chosen from the candidates: those whose two studies are
 * alike in at least `minimum` of `fields` ways, both reporting each, with sample sizes alike
 * within `size_ratio`, and no study in more than `per_study` pairs.
 */
export interface PairSelection {
  minimum: number;
  fields: number;
  size_ratio: number;
  per_study: number | null;
  unclassified?: number;
}
export interface StageCost {
  stage: string;
  dollars: number;
}
export interface EvalResult {
  eval: string;
  metric: string;
  value: number;
  n: number;
  low: number | null;
  high: number | null;
}
