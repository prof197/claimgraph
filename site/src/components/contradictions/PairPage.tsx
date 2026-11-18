// One genuine contradiction: both findings side by side, and a row per field the classifier
// reasons about, marking which differ and which it named as the likely reason.
import { ExternalLink } from "../../lib/ExternalLink";
import { PageFooter } from "../shell/Frame";
import type { ReactNode } from "react";
import { studiesByKey } from "../../lib/data";
import { href } from "../../routes";
import type {
  ContradictionEdge,
  Dimension,
  Export,
  FindingNode,
  FoundWords,
  Outcome,
  Reason,
  Study,
} from "../../types/export";
import styles from "./Contradictions.module.css";
import {
  DESIGN_LABEL,
  DESIGN_NOUN,
  REASON_LABEL,
  citation,
  comparatorText,
  dimensionValue,
  formatCount,
  foundLabel,
  inSentence,
  pubmedUrl,
  sampleSize,
  studyLabel,
} from "./labels";
import { findingsById, orderedPairs, sidesOf } from "./pairs";
import { StudyLetter } from "./StudyLetter";

export interface PairPageProps {
  data: Export;
  pairId: string;
}

interface Found {
  pair: ContradictionEdge;
  outcome: Outcome;
  sides: [FindingNode, FindingNode];
}

function findPair(data: Export, pairId: string): Found | undefined {
  for (const outcome of data.outcomes) {
    const pair = orderedPairs(data, outcome).find((p) => p.pair_id === pairId);
    if (pair !== undefined) {
      const sides = sidesOf(findingsById(data, outcome), pair);
      return sides === undefined ? undefined : { pair, outcome, sides };
    }
  }
  return undefined;
}

export function PairPage({ data, pairId }: PairPageProps) {
  const found = findPair(data, pairId);
  const back =
    found === undefined
      ? href({ page: "question", outcome: null })
      : href({ page: "question", outcome: found.outcome.name, at: "contradictions" });
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <span className={styles.brand}>Claimgraph</span>
          <a className={styles.headerLink} href={back}>
            Back to where studies disagree
          </a>
        </div>
      </header>
      <main className={styles.main}>
        {found === undefined ? (
          <section className={styles.hero} data-state="not-found">
            <h1 className={styles.title}>No such pair</h1>
            <p className={styles.pageLead}>No genuine contradiction on this page has the id {pairId}.</p>
          </section>
        ) : (
          <Pair data={data} found={found} />
        )}
      </main>
      <PageFooter />
    </div>
  );
}

/** Each study's design and badge, in the classifier's order: "One trial: found a benefit." */
function headline(sides: [FindingNode, FindingNode], studies: Map<string, Study>, words: FoundWords): string {
  return sides
    .map((f) => {
      const study = studies.get(f.study);
      const noun = study === undefined ? "study" : DESIGN_NOUN[study.design];
      const label = foundLabel(words, study, f.direction);
      return `One ${noun}: ${label.charAt(0).toLowerCase()}${label.slice(1)}.`;
    })
    .join(" ");
}

function Pair({ data, found }: { data: Export; found: Found }) {
  const { pair, outcome, sides } = found;
  const studies = studiesByKey(data);
  // The panel lists only the closely matched pairs, so the walk through them does too.
  const matched = orderedPairs(data, outcome).filter((p) => p.closely_matched === true);
  const at = matched.findIndex((p) => p.pair_id === pair.pair_id);
  const next = at === -1 ? undefined : matched[(at + 1) % matched.length];
  const reasonWord = REASON_LABEL[pair.reason].toLowerCase();

  return (
    <>
      <section className={styles.hero}>
        <h1 className={styles.h1}>
          <span className={styles.eyebrow}>Contradiction · {outcome.name}</span>
          <span className={styles.title}>{headline(sides, studies, data.question.found)}</span>
        </h1>
        <div className={styles.chips}>
          <span className={styles.reasonChip}>Likely reason: {reasonWord}</span>
          {pair.also_differs.length > 0 && (
            <span className={styles.alsoChip}>
              Also differs: {pair.also_differs.map((r) => REASON_LABEL[r].toLowerCase()).join(", ")}
            </span>
          )}
        </div>
        <p className={styles.pageLead}>{pair.detail}</p>
        <p className={styles.rationale}>{pair.rationale}</p>
      </section>

      <section aria-label="The two findings" className={styles.findings}>
        {sides.map((finding, i) => (
          <FindingCard
            key={finding.id}
            letter={i === 0 ? "A" : "B"}
            finding={finding}
            study={studies.get(finding.study)}
            words={data.question.found}
          />
        ))}
      </section>

      <SideBySide data={data} found={found} studies={studies} />

      <section aria-labelledby="pair-how" className={styles.how}>
        <h2 id="pair-how" className={styles.h2Small}>
          How this pair was found
        </h2>
        <p className={styles.howText}>
          Both findings map to {inSentence(outcome.name)} and point different ways, so the pipeline
          compared them. A model then judged whether the disagreement is genuine and named the most
          likely reason: population, intervention, comparator, outcome measure, or design and risk of
          bias.
        </p>
        <div className={styles.actions}>
          {next !== undefined && matched.length > 1 && (
            <a className={`${styles.action} ${styles.actionStrong}`} href={href({ page: "pair", pairId: next.pair_id })}>
              Next closely matched pair · {formatCount(((at + 1) % matched.length) + 1)} of{" "}
              {formatCount(matched.length)}
            </a>
          )}
          {/* A reader comes here from the panel, so the way back lands on it. */}
          <a className={styles.action} href={href({ page: "question", outcome: outcome.name, at: "contradictions" })}>
            Back to where studies disagree
          </a>
        </div>
      </section>
    </>
  );
}

function FindingCard({
  letter,
  finding,
  study,
  words,
}: {
  letter: "A" | "B";
  finding: FindingNode;
  study: Study | undefined;
  words: FoundWords;
}) {
  return (
    <article className={styles.card} aria-label={studyLabel(study, finding.study)}>
      <span className={`${styles.badge} ${styles.cardBadge} ${styles[finding.direction]}`}>
        {foundLabel(words, study, finding.direction)}
      </span>
      <div className={styles.cardHead}>
        <h2 className={styles.h2Card}>
          <StudyLetter letter={letter} /> {studyLabel(study, finding.study)}
        </h2>
        <p className={styles.cite}>{citation(study)}</p>
      </div>
      <p className={styles.claim}>{finding.claim}</p>
      {finding.subgroup !== null && <p className={styles.subgroup}>Subgroup: {finding.subgroup}</p>}
      <figure className={styles.quoteBox}>
        <figcaption className={styles.quoteLabel}>From the abstract, word for word</figcaption>
        <blockquote className={styles.quote} cite={pubmedUrl(finding.pmid)}>
          {finding.quote}
        </blockquote>
      </figure>
      <ExternalLink className={styles.pubmed} href={pubmedUrl(finding.pmid)}>
        Open on PubMed<span className={styles.visuallyHidden}>: PMID {finding.pmid}</span>
      </ExternalLink>
    </article>
  );
}

type Status = "likely" | "likely-same" | "also" | "different" | "same" | "not-compared" | "one-missing";

interface Row {
  key: string;
  name: ReactNode;
  a: string;
  b: string;
  status: Status;
  note?: string;
}

function same(a: string, b: string): boolean {
  const norm = (s: string) => s.trim().replace(/\s+/g, " ").toLowerCase();
  return norm(a) === norm(b);
}

function reasonStatus(reason: Reason, pair: ContradictionEdge, a: string, b: string): Status {
  const differ = !same(a, b);
  if (reason === pair.reason) return differ ? "likely" : "likely-same";
  if (pair.also_differs.includes(reason)) return "also";
  return differ ? "different" : "same";
}

/** Each status as a one-line verdict, and the qualifier set in smaller text beneath it. */
const STATUS_TEXT: Record<Status, { verdict: string; note?: string }> = {
  likely: { verdict: "Different", note: "likely reason" },
  "likely-same": { verdict: "Same", note: "named as the likely reason" },
  also: { verdict: "Different", note: "also differs" },
  different: { verdict: "Different" },
  same: { verdict: "Same" },
  "not-compared": { verdict: "—" },
  "one-missing": { verdict: "Not compared", note: "reported by one study only" },
};

function rows(found: Found, dimensions: Dimension[], studies: Map<string, Study>): Row[] {
  const { pair, outcome, sides } = found;
  const [fa, fb] = sides;
  const sa = studies.get(fa.study);
  const sb = studies.get(fb.study);
  const field = (key: Reason, name: string, a: string, b: string): Row => ({
    key,
    name,
    a,
    b,
    status: reasonStatus(key, pair, a, b),
  });
  const design = (s: Study | undefined) => (s === undefined ? "Not known" : DESIGN_LABEL[s.design]);
  const measure = field("outcome_measure", "Outcome measure", fa.outcome_reported, fb.outcome_reported);
  if (measure.status === "different") {
    measure.note = `both map to ${inSentence(outcome.name)}`;
  }
  const na = sa?.sample_size ?? null;
  const nb = sb?.sample_size ?? null;
  const result: Row[] = [
    field("population", "Population", fa.population, fb.population),
    field("intervention", "Intervention", fa.intervention, fb.intervention),
    field("comparator", "Comparator", comparatorText(fa.comparator), comparatorText(fb.comparator)),
    field("design_or_bias", "Design or risk of bias", design(sa), design(sb)),
    {
      key: "sample_size",
      name: "Sample size",
      a: sampleSize(na),
      b: sampleSize(nb),
      status: na === null && nb === null ? "not-compared" : na === null || nb === null ? "one-missing" : na === nb ? "same" : "different",
    },
    measure,
  ];
  for (const dimension of dimensions) {
    const va = fa.dimensions[dimension.name] ?? null;
    const vb = fb.dimensions[dimension.name] ?? null;
    const missing = (v: string | number | null) => v === null || v === "not_reported";
    const status: Status =
      missing(va) && missing(vb)
        ? "not-compared"
        : missing(va) || missing(vb)
          ? "one-missing"
          : String(va) === String(vb)
            ? "same"
            : "different";
    result.push({
      key: `dimension-${dimension.name}`,
      name: (
        <span className={styles.rowName}>
          {/* The short label leaves the two studies' columns room; a screen reader hears the full one. */}
          <span className={styles.fieldName}>
            {(dimension.short_label ?? null) === null ? (
              dimension.label
            ) : (
              <>
                <span aria-hidden="true">{dimension.short_label}</span>
                <span className={styles.visuallyHidden}>{dimension.label}</span>
              </>
            )}
          </span>
          <span className={styles.tag}>Custom</span>
          {!dimension.evaluated && <span className={styles.tag}>Not evaluated</span>}
        </span>
      ),
      a: dimensionValue(va, dimension),
      b: dimensionValue(vb, dimension),
      status,
    });
  }
  result.push({
    key: "year",
    name: "Year",
    a: sa === undefined ? "Not known" : String(sa.year),
    b: sb === undefined ? "Not known" : String(sb.year),
    status: "not-compared",
  });
  return result;
}

function SideBySide({ data, found, studies }: { data: Export; found: Found; studies: Map<string, Study> }) {
  const [fa, fb] = found.sides;
  return (
    <section aria-labelledby="pair-compare" className={styles.compare}>
      <h2 id="pair-compare" className={styles.h2Section}>
        Side by side
      </h2>
      <div className={styles.tableWrap} tabIndex={0} role="region" aria-label="Side by side, scrolls sideways">
        <table className={styles.table}>
          <colgroup>
            <col className={styles.colField} />
            <col className={styles.colStudy} />
            <col className={styles.colStudy} />
            <col className={styles.colVerdict} />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Field</th>
              {[fa, fb].map((f, i) => (
                <th key={f.id} scope="col">
                  <span className={styles.studyHead}>
                    <StudyLetter letter={i === 0 ? "A" : "B"} />
                    <span className={`${styles.badge} ${styles[f.direction]}`}>{foundLabel(data.question.found, studies.get(f.study), f.direction)}</span>
                    {studyLabel(studies.get(f.study), f.study)}
                  </span>
                </th>
              ))}
              <th scope="col">Same or different</th>
            </tr>
          </thead>
          <tbody>
            {rows(found, data.question.dimensions, studies).map((row) => {
              const likely = row.status === "likely" || row.status === "likely-same";
              const differs = likely || row.status === "also" || row.status === "different";
              const text = STATUS_TEXT[row.status];
              const notes = [text.note, row.note].filter((n) => n !== undefined);
              return (
                <tr key={row.key} className={likely ? styles.likely : undefined} data-status={row.status}>
                  <th scope="row">{row.name}</th>
                  <td>{row.a}</td>
                  <td>{row.b}</td>
                  <td className={likely ? styles.differs : differs ? undefined : styles.same}>
                    <span className={styles.verdict}>{text.verdict}</span>
                    {notes.length > 0 && <span className={styles.verdictNote}>{notes.join(" · ")}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
