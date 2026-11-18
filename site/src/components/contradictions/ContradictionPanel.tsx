// Where an outcome's studies disagree: only the closely matched pairs, alike in every field the
// comparison checks, each shown side by side from the extracted fields with its likely reason,
// and beneath them the closely matched pairs that only looked contradictory.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { studiesByKey } from "../../lib/data";
import { href } from "../../routes";
import type {
  ApparentPair,
  ContradictionEdge,
  Export,
  FindingNode,
  FoundWords,
  Outcome,
  Reason,
  Study,
} from "../../types/export";
import styles from "./Contradictions.module.css";
import {
  REASON_LABEL,
  comparatorText,
  formatCount,
  foundLabel,
  inSentence,
  plural,
  sampleSize,
  studyLabel,
} from "./labels";
import { findingsById, orderedApparent, orderedPairs, sidesOf } from "./pairs";

export interface ContradictionPanelProps {
  data: Export;
  outcome: Outcome;
  /** Scroll to the panel and focus its heading, as a link back from a pair page asks. */
  land?: boolean;
}

/** Pairs shown before the reader asks for the rest. */
export const FIRST_PAIRS = 10;

export function ContradictionPanel(props: ContradictionPanelProps) {
  // A new outcome starts collapsed.
  return <Panel key={props.outcome.name} {...props} />;
}

function Panel({ data, outcome, land = false }: ContradictionPanelProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const el = heading.current;
    if (!land || el === null) return;
    el.scrollIntoView?.({ block: "start" });
    el.focus({ preventScroll: true });
  }, [land]);
  const pairs = useMemo(() => orderedPairs(data, outcome).filter((p) => p.closely_matched), [data, outcome]);
  const apparent = useMemo(() => orderedApparent(data, outcome).filter((p) => p.closely_matched), [data, outcome]);
  const findings = useMemo(() => findingsById(data, outcome), [data, outcome]);
  const studies = useMemo(() => studiesByKey(data), [data]);
  const [all, setAll] = useState(false);
  const headingId = useId();
  const words = data.question.found;
  const shown = all ? pairs : pairs.slice(0, FIRST_PAIRS);
  const empty = emptyText(outcome);

  return (
    <section className={styles.panel} aria-labelledby={headingId}>
      <div className={styles.intro}>
        <h2 id={headingId} ref={heading} tabIndex={-1} className={styles.h2}>
          Where studies disagree
        </h2>
        <p className={styles.lead}>
          {pairs.length === 0
            ? `No closely matched studies on ${inSentence(outcome.name)} disagree.`
            : `${plural(pairs.length, "closely matched pair", "closely matched pairs")} of studies on ${inSentence(outcome.name)} found different things.`}
        </p>
        {(pairs.length > 0 || apparent.length > 0) && (
          <>
            <p className={styles.lead}>{matchedText(data)}</p>
            <p className={styles.lead}>{accuracyText(data)}</p>
          </>
        )}
      </div>

      {pairs.length === 0 && empty !== null && (
        <p className={styles.empty} data-state="no-genuine">
          {empty}
        </p>
      )}

      {pairs.length > 0 && (
        <ul className={styles.pairs}>
          {shown.map((pair) => (
            <PairItem key={pair.pair_id} pair={pair} sides={sidesOf(findings, pair)} studies={studies} words={words} />
          ))}
        </ul>
      )}

      {shown.length < pairs.length && (
        <button type="button" className={styles.more} onClick={() => setAll(true)}>
          Show all {formatCount(pairs.length)} pairs
        </button>
      )}

      {apparent.length > 0 && <ApparentList pairs={apparent} findings={findings} studies={studies} words={words} />}
    </section>
  );
}

/** What closely matched means, field by field, and what can still differ. */
function matchedText(data: Export): string {
  const dimensions = data.question.dimensions;
  const ratio = data.method.selection?.size_ratio;
  const fields = [
    "the same design",
    "the same outcome measure",
    ...dimensions.map((d) => `the same ${inSentence(d.label)}`),
    ratio === undefined ? "similar sample sizes" : `sample sizes within ${ratio}× of each other`,
  ];
  const listed = `${fields.slice(0, -1).join(", ")} and ${fields.at(-1) ?? ""}`;
  return (
    `Closely matched means alike in all ${fields.length} ways the comparison checks, each ` +
    `reported by both studies: ${listed}. Dose, duration and who was studied can still differ.`
  );
}

/** The classifier's measured accuracy, stated once for the whole panel. */
function accuracyText(data: Export): string {
  const figure = (metric: string) =>
    data.method.evals.find((e) => e.eval === "classification" && e.metric === metric)?.value;
  const verdict = figure("verdict");
  const reason = figure("reason");
  const read = "A model read each pair, judged whether the disagreement is genuine and named the likely reason.";
  if (verdict === undefined || reason === undefined) {
    return `${read} Its accuracy has not been measured for this page.`;
  }
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  return `${read} Checked against hand-labeled pairs, the verdict is right about ${pct(verdict)} of the time and the reason about ${pct(reason)}.`;
}

function emptyText(outcome: Outcome): string | null {
  if (outcome.candidate_pairs === 0) {
    return "No two studies on it point different ways, so there was nothing to compare.";
  }
  if (outcome.classified_pairs === 0) {
    return outcome.candidate_pairs === 1
      ? "Its pair of studies has not been classified yet."
      : "Its pairs of studies have not been classified yet.";
  }
  return null;
}

/** A detail often opens with its reason's name, as in "population: …", which the line has
 * just said. */
export function withoutReason(detail: string, reason: Reason): string {
  const word = `${REASON_LABEL[reason].toLowerCase()}:`;
  return detail.toLowerCase().startsWith(word) ? detail.slice(word.length).trim() : detail;
}

function Side({ finding, studies, words }: { finding: FindingNode; studies: Map<string, Study>; words: FoundWords }) {
  const study = studies.get(finding.study);
  return (
    <div className={styles.side}>
      <span className={`${styles.badge} ${styles[finding.direction]}`}>{foundLabel(words, study, finding.direction)}</span>
      <span className={styles.sideName}>{studyLabel(study, finding.study)}</span>
      <dl className={styles.fields}>
        <dt>Population</dt>
        <dd>{finding.population}</dd>
        <dt>Intervention</dt>
        <dd>{finding.intervention}</dd>
        <dt>Comparator</dt>
        <dd>{comparatorText(finding.comparator)}</dd>
        <dt>Sample size</dt>
        <dd>{sampleSize(study?.sample_size ?? null)}</dd>
      </dl>
      <blockquote className={styles.sideQuote}>
        <p>{finding.quote}</p>
      </blockquote>
    </div>
  );
}

function Sides({
  sides,
  studies,
  words,
}: {
  sides: [FindingNode, FindingNode];
  studies: Map<string, Study>;
  words: FoundWords;
}) {
  return (
    <div className={styles.sides}>
      <Side finding={sides[0]} studies={studies} words={words} />
      <span className={styles.visuallyHidden}>versus</span>
      <Side finding={sides[1]} studies={studies} words={words} />
    </div>
  );
}

function PairItem({
  pair,
  sides,
  studies,
  words,
}: {
  pair: ContradictionEdge;
  sides: [FindingNode, FindingNode] | undefined;
  studies: Map<string, Study>;
  words: FoundWords;
}) {
  const names =
    sides === undefined
      ? pair.pair_id
      : `${studyLabel(studies.get(sides[0].study), sides[0].study)} and ${studyLabel(studies.get(sides[1].study), sides[1].study)}`;
  return (
    <li className={styles.match}>
      {sides !== undefined && <Sides sides={sides} studies={studies} words={words} />}
      <p className={styles.reason}>
        <span className={styles.reasonName}>Likely reason: {REASON_LABEL[pair.reason].toLowerCase()}.</span>{" "}
        {withoutReason(pair.detail, pair.reason)}{" "}
        <a href={href({ page: "pair", pairId: pair.pair_id })}>
          Compare in full<span className={styles.visuallyHidden}> for {names}</span>
        </a>
      </p>
    </li>
  );
}

function ApparentList({
  pairs,
  findings,
  studies,
  words,
}: {
  pairs: ApparentPair[];
  findings: Map<string, FindingNode>;
  studies: Map<string, Study>;
  words: FoundWords;
}) {
  return (
    <details className={styles.apparent}>
      <summary className={styles.summary}>
        {pairs.length === 1
          ? "1 closely matched pair only looked contradictory"
          : `${formatCount(pairs.length)} closely matched pairs only looked contradictory`}
      </summary>
      <ul className={styles.pairs}>
        {pairs.map((pair) => {
          const sides = sidesOf(findings, { source: pair.finding_a, target: pair.finding_b });
          return (
            <li key={pair.pair_id} className={styles.match}>
              {sides !== undefined && <Sides sides={sides} studies={studies} words={words} />}
              <p className={styles.reason}>
                <span className={styles.reasonName}>Why not: {REASON_LABEL[pair.reason].toLowerCase()}.</span>{" "}
                {withoutReason(pair.detail, pair.reason)}
              </p>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
