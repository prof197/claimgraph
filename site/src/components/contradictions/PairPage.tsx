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
  Direction,
  Export,
  FindingNode,
  Outcome,
  Reason,
  Study,
} from "../../types/export";
import styles from "./Contradictions.module.css";
import {
  DESIGN_LABEL,
  DESIGN_NOUN,
  FOUND_LABEL,
  REASON_LABEL,
  citation,
  dimensionValue,
  formatCount,
  inSentence,
  pubmedUrl,
  sampleSize,
  studyLabel,
} from "./labels";
import { findingsById, orderedPairs, orient } from "./pairs";

export interface PairPageProps {
  data: Export;
  pairId: string;
}

const FOUND_PHRASE: Record<Direction, string> = {
  benefit: "a benefit",
  none: "no effect",
  harm: "harm",
};

interface Found {
  pair: ContradictionEdge;
  outcome: Outcome;
  sides: [FindingNode, FindingNode];
}

function findPair(data: Export, pairId: string): Found | undefined {
  for (const outcome of data.outcomes) {
    const pair = orderedPairs(data, outcome).find((p) => p.pair_id === pairId);
    if (pair !== undefined) {
      const sides = orient(findingsById(data, outcome), pair);
      return sides === undefined ? undefined : { pair, outcome, sides };
    }
  }
  return undefined;
}

export function PairPage({ data, pairId }: PairPageProps) {
  const found = findPair(data, pairId);
  const back = href({ page: "question", outcome: found?.outcome.name ?? null });
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <span className={styles.brand}>Claimgraph</span>
          <a className={styles.headerLink} href={back}>
            Back to all contradictions
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

function headline(sides: [FindingNode, FindingNode], studies: Map<string, Study>): string {
  const [a, b] = sides.map((f) => {
    const design = studies.get(f.study)?.design;
    return design === undefined ? "study" : DESIGN_NOUN[design];
  });
  const second = a === b ? "One" : `One ${b}`;
  return `One ${a} found ${FOUND_PHRASE[sides[0].direction]}. ${second} found ${FOUND_PHRASE[sides[1].direction]}.`;
}

function Pair({ data, found }: { data: Export; found: Found }) {
  const { pair, outcome, sides } = found;
  const studies = studiesByKey(data);
  const sameReason = orderedPairs(data, outcome).filter((p) => p.reason === pair.reason);
  const at = sameReason.findIndex((p) => p.pair_id === pair.pair_id);
  const next = sameReason[(at + 1) % sameReason.length];
  const reasonWord = REASON_LABEL[pair.reason].toLowerCase();

  return (
    <>
      <section className={styles.hero}>
        <h1 className={styles.h1}>
          <span className={styles.eyebrow}>Contradiction · {outcome.name}</span>
          <span className={styles.title}>{headline(sides, studies)}</span>
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
        {sides.map((finding) => (
          <FindingCard key={finding.id} finding={finding} study={studies.get(finding.study)} />
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
          {next !== undefined && sameReason.length > 1 && (
            <a className={`${styles.action} ${styles.actionStrong}`} href={href({ page: "pair", pairId: next.pair_id })}>
              Next {reasonWord} pair · {formatCount(((at + 1) % sameReason.length) + 1)} of{" "}
              {formatCount(sameReason.length)}
            </a>
          )}
          <a className={styles.action} href={href({ page: "question", outcome: outcome.name })}>
            Back to the graph
          </a>
        </div>
      </section>
    </>
  );
}

function FindingCard({ finding, study }: { finding: FindingNode; study: Study | undefined }) {
  return (
    <article className={styles.card} aria-label={studyLabel(study, finding.study)}>
      <span className={`${styles.badge} ${styles.cardBadge} ${styles[finding.direction]}`}>
        {FOUND_LABEL[finding.direction]}
      </span>
      <div className={styles.cardHead}>
        <h2 className={styles.h2Card}>{studyLabel(study, finding.study)}</h2>
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

const STATUS_TEXT: Record<Status, string> = {
  likely: "Different · likely reason",
  "likely-same": "Likely reason",
  also: "Different · also differs",
  different: "Different",
  same: "Same",
  "not-compared": "—",
  "one-missing": "Reported by one only",
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
    field("comparator", "Comparator", fa.comparator, fb.comparator),
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
          {dimension.label}
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
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">{studyLabel(studies.get(fa.study), fa.study)}</th>
              <th scope="col">{studyLabel(studies.get(fb.study), fb.study)}</th>
              <th scope="col">Same or different</th>
            </tr>
          </thead>
          <tbody>
            {rows(found, data.question.dimensions, studies).map((row) => {
              const likely = row.status === "likely" || row.status === "likely-same";
              const differs = likely || row.status === "also" || row.status === "different";
              return (
                <tr key={row.key} className={likely ? styles.likely : undefined} data-status={row.status}>
                  <th scope="row">{row.name}</th>
                  <td>{row.a}</td>
                  <td>{row.b}</td>
                  <td className={likely ? styles.differs : differs ? undefined : styles.same}>
                    {STATUS_TEXT[row.status]}
                    {row.note === undefined ? "" : ` · ${row.note}`}
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
