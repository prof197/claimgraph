// Words and rows the question page shows, derived from one export.
import { findingsOn, studiesByKey } from "../../lib/data";
import { fullCitation, shortCitation } from "../../lib/names";
import type {
  Design,
  Direction,
  Export,
  FindingNode,
  Outcome,
  Study,
  Summary,
  WeightedView,
  Weighting,
} from "../../types/export";

export const DIRECTIONS: Direction[] = ["benefit", "none", "harm"];

export const DIRECTION_LABEL: Record<Direction, string> = {
  benefit: "Benefit",
  none: "No effect",
  harm: "Harm",
};

interface DesignWords {
  /** In the list's design column. */
  short: string;
  /** In the finding card. */
  long: string;
  /** In the strip's design line, after a count. */
  one: string;
  many: string;
}

const DESIGN: Record<Design, DesignWords> = {
  rct: { short: "RCT", long: "Randomized controlled trial", one: "RCT", many: "RCTs" },
  non_randomized_trial: {
    short: "Non-randomized trial",
    long: "Non-randomized trial",
    one: "non-randomized trial",
    many: "non-randomized trials",
  },
  cohort: { short: "Cohort", long: "Cohort study", one: "cohort", many: "cohort" },
  case_control: {
    short: "Case-control",
    long: "Case-control study",
    one: "case-control",
    many: "case-control",
  },
  cross_sectional: {
    short: "Cross-sectional",
    long: "Cross-sectional study",
    one: "cross-sectional",
    many: "cross-sectional",
  },
  case_series: {
    short: "Case series",
    long: "Case series",
    one: "case series",
    many: "case series",
  },
  systematic_review: {
    short: "Systematic review",
    long: "Systematic review",
    one: "systematic review",
    many: "systematic reviews",
  },
  meta_analysis: {
    short: "Meta-analysis",
    long: "Meta-analysis",
    one: "meta-analysis",
    many: "meta-analyses",
  },
  narrative_review: {
    short: "Narrative review",
    long: "Narrative review",
    one: "narrative review",
    many: "narrative reviews",
  },
  case_report: { short: "Case report", long: "Case report", one: "case report", many: "case reports" },
  animal: { short: "Animal", long: "Animal study", one: "animal", many: "animal" },
  in_vitro: { short: "In vitro", long: "In vitro study", one: "in vitro", many: "in vitro" },
  other: { short: "Other", long: "Other design", one: "other", many: "other" },
};

export function designShort(design: Design): string {
  return DESIGN[design].short;
}

export function designLong(design: Design): string {
  return DESIGN[design].long;
}

/** "3 RCTs · 1 cohort · 1 case series" */
export function designLine(summary: Summary): string {
  return summary.designs
    .map((d) => `${d.studies} ${d.studies === 1 ? DESIGN[d.design].one : DESIGN[d.design].many}`)
    .join(" · ");
}

export const WEIGHTING_LABEL: Record<Weighting, string> = {
  design: "Design-weighted",
  unweighted: "Unweighted count",
  sample_size: "Size-aware",
};

export function leadingText(view: WeightedView): string {
  return view.leading === null ? "No direction leads" : `${DIRECTION_LABEL[view.leading]} leads`;
}

/** What the strip says about the sensitivity lines underneath it. */
export function sensitivityNote(summary: Summary): string {
  const changed = summary.views.filter((v) => v.changes_leading);
  const notes: string[] = [];
  if (changed.length === 0) {
    notes.push("Every weighting gives the same leading direction.");
  }
  for (const view of changed) {
    notes.push(
      view.weighting === "sample_size"
        ? "Weighting by sample size changes which direction leads."
        : "Counting every study once changes which direction leads.",
    );
  }
  const sized = summary.views.find((v) => v.weighting === "sample_size");
  if (sized !== undefined && sized.studies < summary.studies) {
    notes.push(
      `The size-aware line counts the ${sized.studies} of ${summary.studies} studies that report a sample size.`,
    );
  }
  return notes.join(" ");
}

export function percent(share: number): string {
  if (share > 0 && share < 0.005) {
    return "under 1%";
  }
  return `${Math.round(share * 100)}%`;
}

export function studies(n: number): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? "study" : "studies"}`;
}

export function number(n: number): string {
  return n.toLocaleString("en-US");
}

export function longDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return new Intl.DateTimeFormat("en-US", { dateStyle: "long", timeZone: "UTC" }).format(date);
}

/** An outcome's name inside a sentence: lower-cased unless it starts with an acronym. */
export function inSentence(name: string): string {
  const [first = "", second = ""] = name;
  return second !== "" && second === second.toUpperCase() && /[A-Z]/.test(second)
    ? name
    : first.toLowerCase() + name.slice(1);
}

export function studyLabel(study: Study): string {
  return shortCitation(study);
}

export const citation = fullCitation;

export function pubmedUrl(pmid: string): string {
  return `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(pmid)}/`;
}

/** Design weights by design, as the summary used them, for the designs on one outcome. */
export function weightsLine(data: Export, summary: Summary): string {
  const weights = new Map<Design, number>();
  for (const study of data.studies) {
    if (study.weight !== null) {
      weights.set(study.design, study.weight);
    }
  }
  return summary.designs
    .flatMap((d) => {
      const w = weights.get(d.design);
      return w === undefined ? [] : [{ design: d.design, w }];
    })
    .sort((a, b) => b.w - a.w)
    .map(({ design, w }) => `${DESIGN[design].one} ${w}`)
    .join(" · ");
}

export type NotCounted = "subgroup" | "unweighted";

/** One study on one outcome, as the list shows it: its headline finding, or for a study with
 * only subgroup findings there, its first one, shown but not counted. */
export interface StudyRow {
  finding: FindingNode;
  study: Study;
  notCounted: NotCounted | null;
}

export const NOT_COUNTED_LABEL: Record<NotCounted, string> = {
  subgroup: "Subgroup results only",
  unweighted: "Design without a weight",
};

export function studyRows(data: Export, outcome: Outcome): StudyRow[] {
  const byKey = studiesByKey(data);
  const first = new Map<string, FindingNode>();
  const headline = new Map<string, FindingNode>();
  for (const finding of findingsOn(data, outcome)) {
    if (!first.has(finding.study)) {
      first.set(finding.study, finding);
    }
    if (finding.headline && !headline.has(finding.study)) {
      headline.set(finding.study, finding);
    }
  }
  const rows: StudyRow[] = [];
  for (const [key, firstFinding] of first) {
    const study = byKey.get(key);
    if (study === undefined) {
      continue;
    }
    const lead = headline.get(key);
    const notCounted: NotCounted | null =
      lead === undefined ? "subgroup" : study.weight === null ? "unweighted" : null;
    rows.push({ finding: lead ?? firstFinding, study, notCounted });
  }
  return rows.sort(
    (a, b) =>
      (b.study.weight ?? -1) - (a.study.weight ?? -1) ||
      (b.study.sample_size ?? -1) - (a.study.sample_size ?? -1) ||
      a.study.year - b.study.year,
  );
}

/** The finding selected before the reader picks one: the largest counted study's. */
export function defaultFinding(rows: StudyRow[]): string | null {
  const counted = rows.filter((r) => r.notCounted === null);
  const pool = counted.length > 0 ? counted : rows;
  const largest = pool.reduce<StudyRow | null>(
    (best, row) =>
      best === null || (row.study.sample_size ?? -1) > (best.study.sample_size ?? -1) ? row : best,
    null,
  );
  return largest === null ? null : largest.finding.id;
}

export function dimensionValue(value: string | number | null | undefined, unit: string | null): string {
  if (value === null || value === undefined || value === "not_reported") {
    return "Not reported";
  }
  const text = typeof value === "number" ? number(value) : value.replaceAll("_", " ");
  return unit === null ? text : `${text} ${unit}`;
}

export function money(dollars: number): string {
  return `$${dollars.toFixed(2)}`;
}
