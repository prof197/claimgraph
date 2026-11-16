import { ExternalLink } from "../../lib/ExternalLink";
import { type ReactNode, useState } from "react";
import type { Export, Method as MethodData, Review } from "../../types/export";
import { citation, longDate, money, number, pubmedUrl } from "./labels";

/** Reviews a page shows before "Show more". */
const REVIEWS_SHOWN = 4;

/** Reviews pool studies already on the page, so they are listed for context, never counted. */
// Systematic reviews and meta-analyses are listed; narrative reviews, which pool no studies
// by a stated method, sit in a collapsed list with their count.
const LISTED_REVIEWS: ReadonlySet<Review["design"]> = new Set(["systematic_review", "meta_analysis"]);

function ReviewItem({ review }: { review: Review }) {
  return (
    <li className="shell-card shell-review">
      <p className="shell-strong shell-small">{citation(review)}</p>
      <p className="shell-review__text">{review.summary ?? review.title}</p>
      <ExternalLink className="shell-small" href={pubmedUrl(review.pmid)}>
        Open on PubMed<span className="shell-visually-hidden">: {review.title}</span>
      </ExternalLink>
    </li>
  );
}

export function Reviews({ reviews }: { reviews: Review[] }) {
  const [all, setAll] = useState(false);
  const listed = reviews.filter((r) => LISTED_REVIEWS.has(r.design));
  const narrative = reviews.filter((r) => !LISTED_REVIEWS.has(r.design));
  const shown = all ? listed : listed.slice(0, REVIEWS_SHOWN);
  return (
    <section className="shell-section" aria-labelledby="rev-h">
      <div className="shell-section__intro">
        <h2 id="rev-h" className="shell-h2 shell-h2--small">
          Reviews and meta-analyses
        </h2>
        <p className="shell-intro">
          Listed for context, not counted above: they pool studies that are already on this page.
        </p>
      </div>
      {listed.length === 0 ? (
        <p className="shell-text">No systematic reviews or meta-analyses were found for this question.</p>
      ) : (
        <ul className="shell-reviews">
          {shown.map((review) => (
            <ReviewItem key={review.pmid} review={review} />
          ))}
        </ul>
      )}
      {!all && listed.length > REVIEWS_SHOWN && (
        <button type="button" className="shell-button" onClick={() => setAll(true)}>
          Show {listed.length - REVIEWS_SHOWN} more
        </button>
      )}
      {narrative.length > 0 && (
        <details className="shell-narrative">
          <summary>
            {narrative.length} narrative {narrative.length === 1 ? "review" : "reviews"}, without
            systematic methods
          </summary>
          <ul className="shell-reviews">
            {narrative.map((review) => (
              <ReviewItem key={review.pmid} review={review} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export function Method({ data }: { data: Export }) {
  const { method, question } = data;
  return (
    <section className="shell-section" aria-labelledby="method-h">
      <h2 id="method-h" tabIndex={-1} className="shell-h2 shell-h2--small">
        How this page was made
      </h2>
      <ol className="shell-steps">
        <Step n="01" title="Retrieve">
          {number(method.records_found)} PubMed records found for a fixed search, retrieved{" "}
          {longDate(question.retrieved)}; {number(method.abstracts_kept)} abstracts kept.
        </Step>
        <Step n="02" title="Extract">
          A model fills fixed fields for each finding, and the quoted sentence must match the
          abstract exactly, for {number(method.studies)} primary studies.
        </Step>
        <Step n="03" title="Normalize">
          Outcome names merge through a mapping{" "}
          {question.mapping_reviewed ? "a person reviewed" : "that has not been reviewed yet"}, into{" "}
          {data.outcomes.length} {data.outcomes.length === 1 ? "outcome" : "outcomes"}.
        </Step>
        <Step n="04" title="Compare">
          Only findings on the same outcome that point different ways are compared, and a model
          names the likely reason: {number(method.classified_pairs)} of{" "}
          {number(method.candidate_pairs)} candidate pairs classified.
        </Step>
      </ol>
      {method.drops.length > 0 && (
        <p className="shell-text">
          Left out:{" "}
          {method.drops.map((d) => `${number(d.count)} ${d.what}`).join(" · ")}.
        </p>
      )}
      <p className="shell-text">
        {depthLine(method)} {costLine(method)}
      </p>
      <Evals method={method} />
    </section>
  );
}

function Step({ n, title, children }: { n: string; title: string; children: ReactNode }) {
  return (
    <li className="shell-card shell-step">
      <span className="shell-mono shell-muted" aria-hidden="true">
        {n}
      </span>
      <span className="shell-step__title">{title}</span>
      <span className="shell-step__text">{children}</span>
    </li>
  );
}

function depthLine(method: MethodData): string {
  return method.depth === null
    ? "No contradictions were classified for this page."
    : `Contradictions on this page were classified at ${method.depth} depth.`;
}

function costLine(method: MethodData): string {
  if (method.costs.length === 0) {
    return "No model costs were recorded for this run.";
  }
  const total = method.costs.reduce((sum, c) => sum + c.dollars, 0);
  const parts = method.costs.map((c) => `${c.stage} ${money(c.dollars)}`).join(" · ");
  return `This run cost ${money(total)}, measured: ${parts}.`;
}

function Evals({ method }: { method: MethodData }) {
  return (
    <div className="shell-evals">
      <h3 id="evals-h" tabIndex={-1} className="shell-h3">
        Measured accuracy
      </h3>
      {method.evals.length === 0 ? (
        <p className="shell-text">No evals were recorded for this run.</p>
      ) : (
        <ul className="shell-evals__list">
          {method.evals.map((e) => (
            <li key={`${e.eval}|${e.metric}`}>
              <span className="shell-strong">
                {e.eval}, {e.metric}:
              </span>{" "}
              {e.value.toFixed(2)}
              {e.low !== null && e.high !== null && ` (${e.low.toFixed(2)}–${e.high.toFixed(2)})`},
              n {number(e.n)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
