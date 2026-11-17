// How the findings line up: a band per direction, publication year across, one dot per study.
// The drawing is hidden from assistive technology; over it, every dot is a real button, one tab
// stop for the whole chart and arrow keys between dots. The list view is the other path.
import { type KeyboardEvent, useId, useMemo, useRef, useState } from "react";
import type { Design, Direction, Outcome } from "../../types/export";
import {
  DIRECTION_LABEL,
  type StudyRow,
  designLong,
  designShort,
  inSentence,
  number,
  percent,
  studyLabel,
} from "../shell/labels";
import styles from "./BandChart.module.css";
import {
  AXIS,
  type Axis,
  type Band,
  COMPACT_HEAD,
  type Mark,
  type ZoneStats,
  designsPresent,
  keyboardOrder,
  layoutBands,
  solidity,
  zoneStats,
} from "./layout";
import { useSize } from "./useSize";

export interface BandChartProps {
  outcome: Outcome;
  rows: StudyRow[];
  /** Every outcome's rows, so the dots are sized alike on every outcome. */
  everyOutcome: StudyRow[][];
  /** The year the abstracts were retrieved, where the axis ends. */
  through: number;
  selected: string | null;
  onSelect: (findingId: string) => void;
}

/** "12 studies · 30% weighted", or just the count when nothing is counted. */
export function statsLine(stats: ZoneStats): string {
  const count = `${number(stats.studies)} ${stats.studies === 1 ? "study" : "studies"}`;
  return stats.studies > 0 && stats.share !== null ? `${count} · ${percent(stats.share)} weighted` : count;
}

function accessibleName(mark: Mark): string {
  const { study, notCounted } = mark.row;
  const parts = [
    DIRECTION_LABEL[mark.direction],
    designLong(study.design).toLowerCase(),
    `n ${study.sample_size === null ? "not reported" : number(study.sample_size)}`,
  ];
  if (notCounted === "subgroup") parts.push("subgroup results only, not counted");
  if (notCounted === "unweighted") parts.push("design without a weight, not counted");
  return `${studyLabel(study)}: ${parts.join(", ")}`;
}

export function BandChart({ outcome, rows, everyOutcome, through, selected, onSelect }: BandChartProps) {
  const [only, setOnly] = useState<ReadonlySet<Design>>(new Set());
  const [focusId, setFocusId] = useState<string | null>(null);
  const [box, { width, height: room }] = useSize<HTMLDivElement>();
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const descId = useId();
  const layout = useMemo(
    () => layoutBands(rows, everyOutcome, through, width, room),
    [rows, everyOutcome, through, width, room],
  );
  const order = useMemo(() => (layout === null ? [] : keyboardOrder(layout.bands)), [layout]);
  const stats = zoneStats(rows, only);
  const name = inSentence(outcome.name);

  if (rows.length === 0) {
    return (
      <div className={styles.root}>
        <p className={styles.empty}>No findings on {name} to draw.</p>
      </div>
    );
  }

  const ids = new Set(order.map((m) => m.id));
  const tabStop =
    (focusId !== null && ids.has(focusId) ? focusId : undefined) ??
    (selected !== null && ids.has(selected) ? selected : undefined) ??
    order[0]?.id;

  const move = (event: KeyboardEvent<HTMLButtonElement>, mark: Mark) => {
    const at = order.findIndex((m) => m.id === mark.id);
    const target =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? order[(at + 1) % order.length]
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? order[(at - 1 + order.length) % order.length]
          : event.key === "Home"
            ? order[0]
            : event.key === "End"
              ? order.at(-1)
              : undefined;
    if (target === undefined) return;
    event.preventDefault();
    setFocusId(target.id);
    buttons.current.get(target.id)?.focus();
  };

  const counts = (d: Direction) => layout?.bands.find((b) => b.direction === d)?.marks.length ?? 0;

  return (
    <div className={styles.root}>
      <p id={descId} className={styles.visuallyHidden}>
        {number(rows.length)} {rows.length === 1 ? "study" : "studies"} on {name} in three bands by
        what they found: {counts("benefit")} benefit, {counts("none")} no effect, {counts("harm")}{" "}
        harm, each placed by publication year. Arrow keys move between studies; Enter selects one.
      </p>
      <div ref={box} className={styles.canvas}>
        {layout !== null && (
          <svg
            width={width}
            height={layout.height}
            className={styles.svg}
            role="group"
            aria-label={`Findings on ${outcome.name}, as a graph`}
            aria-describedby={descId}
            data-testid="graph"
          >
            <g aria-hidden="true">
              {layout.bands.map((b) => (
                <rect
                  key={b.direction}
                  x={0}
                  y={b.top}
                  width={width}
                  height={b.height}
                  className={styles[`tint_${b.direction}`]}
                  data-band={b.direction}
                />
              ))}
              {layout.axis.ticks.map((t) => (
                <g key={t.at}>
                  {t.boundary && (
                    <line x1={t.at} x2={t.at} y1={AXIS - 18} y2={layout.height - 4} className={styles.gridline} />
                  )}
                  {t.label !== null && (
                    <text
                      x={t.boundary ? t.at + 3 : t.at}
                      y={AXIS - 9}
                      textAnchor={t.boundary ? "start" : "middle"}
                      className={styles.tick}
                    >
                      {t.label}
                    </text>
                  )}
                </g>
              ))}
              {layout.bands.map((b) => (
                <BandLabel key={b.direction} band={b} stats={stats[b.direction]} compact={layout.compact} axis={layout.axis} />
              ))}
              {layout.bands.map((b) =>
                b.marks.map((m) => (
                  <circle
                    key={m.id}
                    cx={m.x}
                    cy={b.mid + m.y}
                    r={m.r}
                    className={`${styles.dot} ${styles[`dot_${m.direction}`]} ${m.row.notCounted === null ? "" : styles.uncounted} ${m.id === selected ? styles.picked : ""}`}
                    style={{
                      fillOpacity: solidity(m.row.study.weight),
                      opacity: only.size > 0 && !only.has(m.row.study.design) ? 0.12 : 1,
                    }}
                    data-node={m.id}
                  />
                )),
              )}
            </g>
            {layout.bands.map((b) =>
              b.marks.map((m) => {
                const hit = Math.max(2 * m.r + 4, 12);
                return (
                  <foreignObject key={m.id} x={m.x - hit / 2} y={b.mid + m.y - hit / 2} width={hit} height={hit} className={styles.hit}>
                    <button
                      ref={(el) => {
                        if (el === null) buttons.current.delete(m.id);
                        else buttons.current.set(m.id, el);
                      }}
                      type="button"
                      className={styles.node}
                      tabIndex={m.id === tabStop ? 0 : -1}
                      aria-pressed={m.id === selected}
                      aria-label={accessibleName(m)}
                      title={`${studyLabel(m.row.study)}, n ${m.row.study.sample_size === null ? "not reported" : number(m.row.study.sample_size)}`}
                      onClick={() => onSelect(m.id)}
                      onFocus={() => setFocusId(m.id)}
                      onKeyDown={(event) => move(event, m)}
                    />
                  </foreignObject>
                );
              }),
            )}
          </svg>
        )}
      </div>
      <Legend rows={rows} only={only} onOnly={setOnly} />
    </div>
  );
}

function BandLabel({ band, stats, compact, axis }: { band: Band; stats: ZoneStats; compact: boolean; axis: Axis }) {
  const lines = statsLine(stats).split(" · ");
  const name = (
    <tspan className={`${styles.bandName} ${styles[`text_${band.direction}`]}`}>{DIRECTION_LABEL[band.direction]}</tspan>
  );
  const empty = band.marks.length === 0 && (
    <text x={compact ? axis.right : (axis.left + axis.right) / 2} y={band.top + band.height / 2 + 4} textAnchor={compact ? "end" : "middle"} className={styles.bandMeta}>
      No studies
    </text>
  );
  if (compact) {
    const y = band.marks.length === 0 ? band.top + band.height / 2 + 4 : band.top + COMPACT_HEAD - 6;
    return (
      <>
        <text x={axis.left} y={y}>
          {name}
          <tspan className={styles.bandMeta} dx={8}>
            {lines.join(" · ")}
          </tspan>
        </text>
        {empty}
      </>
    );
  }
  // The name and its lines sit as one block centred in the band.
  const first = band.top + band.height / 2 - (15 * lines.length - 7) / 2;
  return (
    <>
      <text x={12} y={first}>
        {name}
      </text>
      {lines.map((line, i) => (
        <text key={line} x={12} y={first + 16 + 15 * i} className={styles.bandMeta}>
          {line}
        </text>
      ))}
      {empty}
    </>
  );
}

function Legend({
  rows,
  only,
  onOnly,
}: {
  rows: StudyRow[];
  only: ReadonlySet<Design>;
  onOnly: (next: ReadonlySet<Design>) => void;
}) {
  const toggle = (d: Design) => {
    const next = new Set(only);
    if (next.has(d)) next.delete(d);
    else next.add(d);
    onOnly(next);
  };
  return (
    <div className={styles.legend}>
      <div className={styles.chips} role="group" aria-label="Highlight designs">
        {designsPresent(rows).map(({ design, weight }) => (
          <button
            key={design}
            type="button"
            className={styles.chip}
            title={designLong(design)}
            aria-pressed={only.has(design)}
            onClick={() => toggle(design)}
          >
            <svg width="14" height="14" aria-hidden="true">
              <circle cx="7" cy="7" r="5.5" className={styles.chipDot} style={{ fillOpacity: solidity(weight) }} />
            </svg>
            {designShort(design)}
          </button>
        ))}
      </div>
      <div className={styles.noteRow}>
        <p className={styles.note}>
          Solid is stronger design. Bigger dots are bigger studies. Dashed: not counted.
        </p>
        {only.size > 0 && (
          <button type="button" className={styles.linkButton} onClick={() => onOnly(new Set())}>
            Show all
          </button>
        )}
      </div>
    </div>
  );
}
