import { useState } from "react";
import type { Direction, Export, Outcome, WeightedView } from "../../types/export";
import {
  DIRECTION_LABEL,
  DIRECTIONS,
  WEIGHTING_LABEL,
  designLine,
  inSentence,
  leadingText,
  number,
  percent,
  sensitivityNote,
  studies,
  weightsLine,
} from "./labels";

export function SummaryStrip({ data, outcome }: { data: Export; outcome: Outcome }) {
  const summary = outcome.summary;
  const design = summary.views.find((v) => v.weighting === "design");
  const thin = summary.thin || design === undefined;
  const notCounted = [
    summary.subgroup_only > 0 ? `${studies(summary.subgroup_only)} with subgroup results only` : null,
    summary.uncounted > 0 ? `${studies(summary.uncounted)} of a design without a weight` : null,
  ].filter((s): s is string => s !== null);

  return (
    <section className="shell-card shell-strip" aria-label={`Summary for ${inSentence(outcome.name)}`}>
      <div className="shell-strip__col">
        <p className="shell-strip__count">{number(summary.studies)}</p>
        <p className="shell-strip__lede">
          primary {summary.studies === 1 ? "study" : "studies"} measured {inSentence(outcome.name)}
        </p>
        <p className="shell-mono shell-muted">{designLine(summary)}</p>
        {notCounted.length > 0 && (
          <p className="shell-small shell-muted">Shown but not counted: {notCounted.join(" · ")}.</p>
        )}
      </div>
      {thin ? (
        <div className="shell-strip__col">
          <p className="shell-strip__title">What they found</p>
          <Legend counts={summary.counts} />
          <p className="shell-thin">
            <strong>Thin evidence.</strong> Too few studies measured this outcome to weigh them, so
            the page shows counts only.
          </p>
        </div>
      ) : (
        <>
          <div className="shell-strip__col">
            <p className="shell-strip__title">What they found, weighted by study design</p>
            <WeightedBar view={design} />
            <Legend counts={summary.counts} />
            <p className="shell-small shell-muted">Weights: {weightsLine(data, summary)}.</p>
          </div>
          <div className="shell-strip__col">
            <p className="shell-strip__title">Does the weighting change the picture?</p>
            <dl className="shell-sensitivity">
              {summary.views.map((view) => (
                <div key={view.weighting} className="shell-sensitivity__row">
                  <dt>{WEIGHTING_LABEL[view.weighting]}</dt>
                  <dd>
                    <span className={view.changes_leading ? "shell-strong" : undefined}>
                      {leadingText(view)}
                    </span>
                    {view.changes_leading && <span className="shell-badge">Changes</span>}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="shell-small shell-muted">{sensitivityNote(summary)}</p>
          </div>
        </>
      )}
    </section>
  );
}

function Legend({ counts }: { counts: Record<Direction, number> }) {
  return (
    <ul className="shell-legend">
      {DIRECTIONS.map((d) => (
        <li key={d}>
          <span aria-hidden="true" className={`shell-swatch shell-swatch--${d}`} />
          {DIRECTION_LABEL[d]} · {studies(counts[d])}
        </li>
      ))}
    </ul>
  );
}

/** The design-weighted shares, in a fixed order so position carries direction as well as
 * colour. Each segment says its share on hover and on keyboard focus. */
export function WeightedBar({ view }: { view: WeightedView }) {
  const [active, setActive] = useState<Direction | null>(null);
  const segments = DIRECTIONS.filter((d) => view.shares[d] > 0);
  const describe = (d: Direction) =>
    `${DIRECTION_LABEL[d]}: ${percent(view.shares[d])} of the design-weighted evidence`;

  return (
    <div className="shell-bar-wrap">
      <div role="group" aria-label="Design-weighted share by direction" className="shell-bar">
        {segments.map((d) => (
          <span
            key={d}
            role="img"
            tabIndex={0}
            aria-label={describe(d)}
            title={describe(d)}
            className={`shell-bar__seg shell-swatch--${d}`}
            style={{ flexGrow: view.shares[d] }}
            onMouseEnter={() => setActive(d)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(d)}
            onBlur={() => setActive(null)}
          />
        ))}
      </div>
      <p className="shell-small shell-bar__readout" aria-hidden="true">
        {active === null ? "Hover over or focus a segment for its share." : describe(active)}
      </p>
    </div>
  );
}
