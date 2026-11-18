import { fullCitation, shortCitation } from "../../lib/names";
// The words the panel and the pair page use for studies, designs and directions.
import type { Design, Dimension, Direction, FoundWords, Reason, Study } from "../../types/export";

export const REASONS: readonly Reason[] = [
  "population",
  "intervention",
  "comparator",
  "outcome_measure",
  "design_or_bias",
  "unclear",
];

export const REASON_LABEL: Record<Reason, string> = {
  population: "Population",
  intervention: "Intervention",
  comparator: "Comparator",
  outcome_measure: "Outcome measure",
  design_or_bias: "Design or risk of bias",
  unclear: "Unclear",
};

// "No clear difference" is GRADE's wording for a result that is not significant, which is
// mostly what a null finding here is: it does not claim the exposure has no effect.
export const DIRECTION_LABEL: Record<Direction, string> = {
  benefit: "Benefit",
  none: "No clear difference",
  harm: "Harm",
};

/** A study's badge, in the question's words: a trial found a difference or not; an
 * observational study found a link, which is not a benefit. */
export function foundLabel(words: FoundWords, study: Study | undefined, direction: Direction): string {
  return (study?.trial === true ? words.trial : words.observational)[direction];
}

/** Extraction writes "Across participants" for an association with no comparison group. */
export function comparatorText(comparator: string): string {
  return /across participants/i.test(comparator)
    ? "No comparison group (association across participants)"
    : comparator;
}

export const DESIGN_LABEL: Record<Design, string> = {
  rct: "Randomized controlled trial",
  non_randomized_trial: "Non-randomized trial",
  cohort: "Cohort",
  case_control: "Case-control",
  cross_sectional: "Cross-sectional",
  case_series: "Case series",
  systematic_review: "Systematic review",
  meta_analysis: "Meta-analysis",
  narrative_review: "Narrative review",
  case_report: "Case report",
  animal: "Animal study",
  in_vitro: "In vitro study",
  other: "Other design",
};

/** The noun a sentence uses for a study of this design: "One trial found a benefit." */
export const DESIGN_NOUN: Record<Design, string> = {
  rct: "trial",
  non_randomized_trial: "trial",
  cohort: "cohort study",
  case_control: "case-control study",
  cross_sectional: "cross-sectional study",
  case_series: "case series",
  systematic_review: "review",
  meta_analysis: "meta-analysis",
  narrative_review: "review",
  case_report: "case report",
  animal: "animal study",
  in_vitro: "lab study",
  other: "study",
};

/** A short name for a study, as a reader would cite it. */
export function studyLabel(study: Study | undefined, key: string): string {
  return study === undefined ? `PMID ${key}` : shortCitation(study);
}

export function citation(study: Study | undefined): string {
  return study === undefined ? "" : fullCitation(study);
}

export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}

export function sampleSize(n: number | null): string {
  return n === null ? "Not reported" : formatCount(n);
}

/** "n 120", or "n not reported". */
export function nShort(n: number | null): string {
  return n === null ? "n not reported" : `n ${formatCount(n)}`;
}

export function plural(n: number, one: string, many: string): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}

/** An outcome name inside a sentence: "on synthetic symptoms", leaving acronyms alone. */
export function inSentence(name: string): string {
  const [first = "", second = ""] = name;
  return second !== "" && second === second.toLowerCase() ? first.toLowerCase() + name.slice(1) : name;
}

export function pubmedUrl(pmid: string): string {
  return `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`;
}

/** A custom dimension's value as the page shows it, with its unit. */
export function dimensionValue(value: string | number | null | undefined, dimension: Dimension): string {
  if (value === null || value === undefined) {
    return "Not reported";
  }
  if (typeof value === "number") {
    const shown = formatCount(value);
    return dimension.unit === null ? shown : `${shown} ${dimension.unit}`;
  }
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
