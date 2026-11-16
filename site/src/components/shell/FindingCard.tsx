import type { ReactNode } from "react";
import { ExternalLink } from "../../lib/ExternalLink";
import { href } from "../../routes";
import type { Export, FindingNode, Outcome } from "../../types/export";
import {
  DIRECTION_LABEL,
  citation,
  designLong,
  dimensionValue,
  inSentence,
  number,
  pubmedUrl,
} from "./labels";

export interface FindingCardProps {
  data: Export;
  outcome: Outcome;
  finding: FindingNode | undefined;
  /** Called before following an "Also reports" link, with the outcome and the study's finding
   * there, so that tab opens on the same study. */
  onAlso: (outcome: string, findingId: string) => void;
}

/** A fixed-height card: title and citation pinned at the top, actions at the bottom, and only
 * the details between them scroll. */
export function FindingCard({ data, outcome, finding, onAlso }: FindingCardProps) {
  const study = finding && data.studies.find((s) => s.key === finding.study);
  if (finding === undefined || study === undefined) {
    return (
      <aside className="shell-card shell-detail" aria-labelledby="detail-h">
        <div className="shell-detail__head">
          <p className="shell-eyebrow">Selected finding</p>
          <h3 id="detail-h" className="shell-detail__title">
            No finding selected
          </h3>
        </div>
        <div className="shell-detail__body">
          <p>Select a finding in the graph or the list to see it here.</p>
        </div>
      </aside>
    );
  }

  const elsewhere = data.outcomes.flatMap((other) => {
    if (other.name === outcome.name) {
      return [];
    }
    const there = data.graph.nodes.filter(
      (n): n is FindingNode => n.kind === "finding" && n.outcome === other.name && n.study === study.key,
    );
    const pick = there.find((n) => n.headline) ?? there[0];
    return pick === undefined ? [] : [{ outcome: other.name, findingId: pick.id }];
  });

  const rows: [string, ReactNode][] = [
    [
      "Direction",
      <span className="shell-direction">
        <span aria-hidden="true" className={`shell-dot shell-swatch--${finding.direction}`} />
        {DIRECTION_LABEL[finding.direction]}
      </span>,
    ],
    ["Outcome", outcome.name],
    ["Measured as", finding.outcome_reported],
    ...(finding.subgroup === null ? [] : ([["Subgroup", finding.subgroup]] as [string, ReactNode][])),
    ["Population", finding.population],
    ["Intervention", finding.intervention],
    ["Comparator", finding.comparator],
    ["Design", designLong(study.design)],
    ["Sample size", study.sample_size === null ? "Not reported" : number(study.sample_size)],
    ["Year", String(study.year)],
    ...data.question.dimensions.map((dim): [string, ReactNode] => [
      dim.label,
      <span className="shell-direction">
        {dimensionValue(finding.dimensions[dim.name], dim.unit)}
        <span className="shell-badge shell-badge--quiet">Custom</span>
        {!dim.evaluated && <span className="shell-badge shell-badge--quiet">Not evaluated</span>}
      </span>,
    ]),
  ];

  return (
    <aside className="shell-card shell-detail" aria-labelledby="detail-h">
      <div className="shell-detail__head">
        <p className="shell-eyebrow">Selected finding</p>
        <h3 id="detail-h" className="shell-detail__title">
          {study.title}
        </h3>
        <p className="shell-small shell-muted">{citation(study)}</p>
      </div>
      <div className="shell-detail__body" role="region" aria-label="Finding details" tabIndex={0}>
        <p className="shell-detail__claim">{finding.claim}</p>
        <figure className="shell-quote">
          <figcaption className="shell-eyebrow">From the abstract, word for word</figcaption>
          <blockquote cite={pubmedUrl(finding.pmid)}>
            <p>{finding.quote}</p>
          </blockquote>
        </figure>
        <dl className="shell-fields">
          {rows.map(([label, value]) => (
            <div key={label} className="shell-fields__row">
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="shell-detail__actions">
        <ExternalLink className="shell-action shell-action--primary" href={pubmedUrl(finding.pmid)}>
          Open on PubMed
        </ExternalLink>
        {elsewhere.map((e) => (
          <a
            key={e.outcome}
            className="shell-action"
            href={href({ page: "question", outcome: e.outcome })}
            onClick={() => onAlso(e.outcome, e.findingId)}
          >
            Also reports: {inSentence(e.outcome)}
          </a>
        ))}
      </div>
    </aside>
  );
}
