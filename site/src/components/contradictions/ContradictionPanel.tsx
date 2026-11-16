// Where an outcome's studies disagree: genuine contradictions grouped by their likely reason,
// strongest first, and beneath them the pairs that only looked contradictory.
import { useId, useMemo, useState } from "react";
import { studiesByKey } from "../../lib/data";
import { href } from "../../routes";
import type { ContradictionEdge, Export, FindingNode, Outcome, Reason, Study } from "../../types/export";
import styles from "./Contradictions.module.css";
import {
  DESIGN_LABEL,
  FOUND_LABEL,
  REASON_LABEL,
  formatCount,
  inSentence,
  nShort,
  plural,
  studyLabel,
} from "./labels";
import { findingsById, groupByReason, orderedApparent, orderedPairs, orient } from "./pairs";

export interface ContradictionPanelProps {
  data: Export;
  outcome: Outcome;
}

/** Pairs shown per reason before the reader asks for more. */
export const FIRST_PAIRS = 3;
/** Up to this many pairs, one click shows them all; past it, each click adds a page. */
const SHOW_ALL_UP_TO = 200;
const PAGE = 50;
const FIRST_APPARENT = 50;

export function ContradictionPanel(props: ContradictionPanelProps) {
  // A new outcome starts unfiltered and collapsed.
  return <Panel key={props.outcome.name} {...props} />;
}

function Panel({ data, outcome }: ContradictionPanelProps) {
  const pairs = useMemo(() => orderedPairs(data, outcome), [data, outcome]);
  const apparent = useMemo(() => orderedApparent(data, outcome), [data, outcome]);
  const findings = useMemo(() => findingsById(data, outcome), [data, outcome]);
  const studies = useMemo(() => studiesByKey(data), [data]);
  const [filter, setFilter] = useState<Reason | null>(null);
  const [limit, setLimit] = useState(FIRST_PAIRS);
  const headingId = useId();

  const groups = groupByReason(pairs);
  const shown = filter === null ? groups : groups.filter((g) => g.reason === filter);
  const total = shown.reduce((sum, g) => sum + g.pairs.length, 0);
  const hidden = shown.reduce((sum, g) => sum + Math.max(0, g.pairs.length - limit), 0);

  return (
    <section className={styles.panel} aria-labelledby={headingId}>
      <div className={styles.intro}>
        <h2 id={headingId} className={styles.h2}>
          Where studies disagree
        </h2>
        <p className={styles.lead}>{introText(pairs, outcome)}</p>
      </div>

      {pairs.length === 0 ? (
        <p className={styles.empty} data-state="no-genuine">
          {emptyText(outcome)}
        </p>
      ) : (
        <>
          <div role="group" aria-label="Filter by likely reason" className={styles.filters}>
            <button
              type="button"
              className={styles.chip}
              aria-pressed={filter === null}
              onClick={() => setFilter(null)}
            >
              All · {formatCount(pairs.length)}
            </button>
            {groups.map((group) => (
              <button
                key={group.reason}
                type="button"
                className={styles.chip}
                aria-pressed={filter === group.reason}
                onClick={() => setFilter(group.reason)}
              >
                {REASON_LABEL[group.reason]} · {formatCount(group.pairs.length)}
              </button>
            ))}
          </div>

          {shown.map((group) => (
            <div key={group.reason} className={styles.group}>
              <h3 className={styles.h3}>
                {REASON_LABEL[group.reason]}
                <span className={styles.groupCount}>{plural(group.pairs.length, "pair", "pairs")}</span>
              </h3>
              <ul className={styles.pairs}>
                {group.pairs.slice(0, limit).map((pair) => (
                  <PairItem
                    key={pair.pair_id}
                    pair={pair}
                    sides={orient(findings, pair)}
                    studies={studies}
                  />
                ))}
              </ul>
            </div>
          ))}

          {hidden > 0 && (
            <button
              type="button"
              className={styles.more}
              onClick={() =>
                setLimit(total <= SHOW_ALL_UP_TO ? Number.POSITIVE_INFINITY : limit + PAGE)
              }
            >
              {total <= SHOW_ALL_UP_TO
                ? `Show all ${formatCount(total)} pairs`
                : `Show more pairs · ${formatCount(total - hidden)} of ${formatCount(total)} shown`}
            </button>
          )}
        </>
      )}

      {apparent.length > 0 && <ApparentList pairs={apparent} studies={studies} />}
    </section>
  );
}

function introText(pairs: ContradictionEdge[], outcome: Outcome): string {
  if (pairs.length === 0) {
    return `No genuine contradictions on ${inSentence(outcome.name)}.`;
  }
  const effectVsNone = pairs.filter((p) => p.pair_kind === "effect_vs_none").length;
  const benefitVsHarm = pairs.length - effectVsNone;
  const most =
    effectVsNone > benefitVsHarm
      ? " Most pair a study that found an effect with one that found none."
      : benefitVsHarm > effectVsNone
        ? " Most pair a study that found a benefit with one that found harm."
        : "";
  return `${plural(pairs.length, "pair", "pairs")} on ${inSentence(outcome.name)}.${most}${coverage(outcome)}`;
}

/** Says when the classifier read only some of the candidate pairs, so a short list is not
 * mistaken for a settled one. */
function coverage(outcome: Outcome): string {
  if (outcome.classified_pairs >= outcome.candidate_pairs) {
    return "";
  }
  return ` ${formatCount(outcome.classified_pairs)} of ${plural(outcome.candidate_pairs, "candidate pair was", "candidate pairs were")} classified.`;
}

function emptyText(outcome: Outcome): string {
  if (outcome.candidate_pairs === 0) {
    return "No two studies on it point different ways, so there was nothing to compare.";
  }
  if (outcome.classified_pairs === 0) {
    return `Its ${plural(outcome.candidate_pairs, "candidate pair has", "candidate pairs have")} not been classified yet.`;
  }
  return `The classifier read ${plural(outcome.classified_pairs, "candidate pair", "candidate pairs")} and judged none genuine.${coverage(outcome)}`;
}

/** What a side says about the field the pair's reason points at. */
function sideNote(finding: FindingNode, study: Study | undefined, reason: Reason): string {
  switch (reason) {
    case "population":
      return finding.population;
    case "intervention":
      return finding.intervention;
    case "comparator":
      return finding.comparator;
    case "outcome_measure":
      return finding.outcome_reported;
    case "design_or_bias":
    case "unclear":
      return study === undefined ? "Design not known" : DESIGN_LABEL[study.design];
  }
}

function Side({ finding, studies, reason }: { finding: FindingNode; studies: Map<string, Study>; reason: Reason }) {
  const study = studies.get(finding.study);
  return (
    <span className={styles.side}>
      <span className={`${styles.badge} ${styles[finding.direction]}`}>{FOUND_LABEL[finding.direction]}</span>
      <span className={styles.sideName}>{studyLabel(study, finding.study)}</span>
      <span className={styles.sideNote}>
        {sideNote(finding, study, reason)} · {nShort(study?.sample_size ?? null)}
      </span>
    </span>
  );
}

function PairItem({
  pair,
  sides,
  studies,
}: {
  pair: ContradictionEdge;
  sides: [FindingNode, FindingNode] | undefined;
  studies: Map<string, Study>;
}) {
  return (
    <li>
      <a className={styles.pair} href={href({ page: "pair", pairId: pair.pair_id })}>
        {sides === undefined ? (
          <span className={styles.sideName}>{pair.pair_id}</span>
        ) : (
          <>
            <Side finding={sides[0]} studies={studies} reason={pair.reason} />
            <span className={styles.visuallyHidden}>versus</span>
            <Side finding={sides[1]} studies={studies} reason={pair.reason} />
          </>
        )}
        <span className={styles.detail}>
          <span className={styles.visuallyHidden}>Likely reason: {REASON_LABEL[pair.reason].toLowerCase()}. </span>
          {pair.detail}
          {pair.also_differs.length > 0 && (
            <span className={styles.also}>
              Also differs: {pair.also_differs.map((r) => REASON_LABEL[r].toLowerCase()).join(", ")}
            </span>
          )}
        </span>
      </a>
    </li>
  );
}

function ApparentList({
  pairs,
  studies,
}: {
  pairs: Export["outcomes"][number]["apparent"];
  studies: Map<string, Study>;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? pairs : pairs.slice(0, FIRST_APPARENT);
  return (
    <details className={styles.apparent}>
      <summary className={styles.summary}>
        {pairs.length === 1
          ? "1 pair looked contradictory but wasn't"
          : `${formatCount(pairs.length)} pairs looked contradictory but weren't`}
      </summary>
      <ul className={styles.apparentList}>
        {shown.map((pair) => (
          <li key={pair.pair_id}>
            {studyLabel(studies.get(pair.study_a), pair.study_a)} vs{" "}
            {studyLabel(studies.get(pair.study_b), pair.study_b)} · {pair.detail}
          </li>
        ))}
      </ul>
      {shown.length < pairs.length && (
        <button type="button" className={styles.more} onClick={() => setAll(true)}>
          Show all {formatCount(pairs.length)}
        </button>
      )}
    </details>
  );
}
