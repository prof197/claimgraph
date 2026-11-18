import { ExternalLink } from "../../lib/ExternalLink";
import { type ReactNode, useState } from "react";
import type { Dimension, Drop, EvalResult, Export, Method as MethodData, Review } from "../../types/export";
import { citation, longDate, money, number, pubmedUrl } from "./labels";

/** Reviews a page shows before "Show more". */
const REVIEWS_SHOWN = 4;

// The export lists only the reviews whose title or summary names the question's exposure and
// outcome. Systematic reviews and meta-analyses are listed; narrative reviews, which pool no
// studies by a stated method, sit in a collapsed list with their count.
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
          Listed for context, not counted above.
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
          judges each pair and names the likely reason. {compareLine(method, data.question.dimensions)}
        </Step>
      </ol>
      {method.drops.length > 0 && (
        <p className="shell-text">
          Left out:{" "}
          {method.drops.map(dropText).join(" · ")}.
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

/** Which pairs the model judged, and which of them the page shows. Candidate counts stay
 * off the page: they count pairs of studies, which reads as a count of findings. */
export function compareLine(method: MethodData, dimensions: Dimension[]): string {
  const selection = method.selection ?? null;
  const ratio = selection === null ? null : selection.size_ratio;
  const fields = [
    "design",
    "outcome measure",
    ...dimensions.map((d) => d.label.charAt(0).toLowerCase() + d.label.slice(1)),
    ratio === null ? "sample size" : `sample size within ${ratio}×`,
  ];
  const listed = `${fields.slice(0, -1).join(", ")} and ${fields.at(-1) ?? ""}`;
  if (method.classified_pairs === 0) {
    return "No pairs have been judged yet.";
  }
  const waiting = selection?.unclassified ?? 0;
  const chosen = method.classified_pairs + waiting;
  const judged =
    selection === null
      ? "It judged every pair"
      : `It judged ${waiting > 0 ? `${number(method.classified_pairs)} of ` : ""}the ${number(chosen)} pairs ` +
        `whose studies are alike in at least ${selection.minimum} of ${selection.fields} ways` +
        (selection.per_study === null ? "" : `, with no study in more than ${selection.per_study}`);
  const rest = waiting === 0 ? "" : ` The other ${number(waiting)} have not been classified yet.`;
  return (
    `${judged}.${rest} The page shows only the closely matched ones, alike in all ${fields.length}, ` +
    `each reported by both studies: ${listed}.`
  );
}

const PLURALS = /\b(findings|records|abstracts|pairs|reviews)\b/;

/** "3 findings the review excluded", or "1 finding …": the first plural noun agrees. */
export function dropText(drop: Drop): string {
  const what = drop.count === 1 ? drop.what.replace(PLURALS, (w) => w.slice(0, -1)) : drop.what;
  return `${number(drop.count)} ${what}`;
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
  const parts = method.costs
    .map((c) => `${c.stage} ${money(c.dollars)} (${c.runs} ${c.runs === 1 ? "run" : "runs"})`)
    .join(" · ");
  return `Measured spend over every recorded run of this question, evals included: ${money(total)}. By stage: ${parts}.`;
}

/** An extraction field scored both ways: matched findings over the model's, and over the labels'. */
interface FieldScore {
  field: string;
  precision: EvalResult;
  recall: EvalResult;
}

type EvalRow = { kind: "one"; result: EvalResult } | ({ kind: "field" } & FieldScore);

function evalRows(evals: EvalResult[]): EvalRow[] {
  const rows: EvalRow[] = [];
  const paired = new Set<EvalResult>();
  for (const e of evals) {
    if (paired.has(e)) continue;
    const field = e.eval === "extraction" ? /^(.*) precision$/.exec(e.metric)?.[1] : undefined;
    const recall = evals.find((r) => field !== undefined && r.eval === e.eval && r.metric === `${field} recall`);
    if (field !== undefined && recall !== undefined) {
      paired.add(recall);
      rows.push({ kind: "field", field, precision: e, recall });
    } else {
      rows.push({ kind: "one", result: e });
    }
  }
  return rows;
}

function matched(e: EvalResult): number {
  return Math.round(e.value * e.n);
}

/** How the extraction fields were scored, and why precision and recall can coincide. */
export function fieldNote(rows: EvalRow[]): string | null {
  const first = rows.find((r): r is { kind: "field" } & FieldScore => r.kind === "field");
  if (first === undefined) return null;
  const returned = first.precision.n;
  const labeled = first.recall.n;
  const same =
    returned === labeled
      ? " The model returned as many findings as were labeled, so each field's two figures are equal; they are not equal by construction."
      : "";
  return (
    "Each finding the model returned is matched to a hand-labeled finding by its quoted sentence, " +
    "and a field counts as right only on a matched finding. Precision is over the " +
    `${number(returned)} findings the model returned, recall over the ${number(labeled)} labeled.${same}`
  );
}

function interval(e: EvalResult): string {
  return e.low !== null && e.high !== null ? ` (${e.low.toFixed(2)}–${e.high.toFixed(2)})` : "";
}

function Evals({ method }: { method: MethodData }) {
  const rows = evalRows(method.evals);
  const note = fieldNote(rows);
  return (
    <div className="shell-evals">
      <h3 id="evals-h" tabIndex={-1} className="shell-h3">
        Measured accuracy
      </h3>
      {method.evals.length === 0 ? (
        <p className="shell-text">No evals were recorded for this run.</p>
      ) : (
        <>
          {note !== null && <p className="shell-text">{note}</p>}
          <ul className="shell-evals__list">
            {rows.map((row) =>
              row.kind === "field" ? (
                <li key={`${row.precision.eval}|${row.field}`}>
                  <span className="shell-strong">
                    {row.precision.eval}, {row.field}:
                  </span>{" "}
                  precision {row.precision.value.toFixed(2)} ({matched(row.precision)} of {number(row.precision.n)}),
                  recall {row.recall.value.toFixed(2)} ({matched(row.recall)} of {number(row.recall.n)})
                </li>
              ) : (
                <li key={`${row.result.eval}|${row.result.metric}`}>
                  <span className="shell-strong">
                    {row.result.eval}, {row.result.metric}:
                  </span>{" "}
                  {row.result.value.toFixed(2)}
                  {interval(row.result)}, n {number(row.result.n)}
                </li>
              ),
            )}
          </ul>
        </>
      )}
    </div>
  );
}
