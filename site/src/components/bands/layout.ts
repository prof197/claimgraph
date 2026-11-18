// The band chart's geometry, kept apart from React so it can be tested: benefit, no effect and
// harm as bands, publication year across, one dot per study. Direction is the band a dot sits
// in, its area the sample size, and how solid it is the design's weight.
import type { Design, Direction } from "../../types/export";
import { DIRECTIONS, type StudyRow } from "../shell/labels";

export interface Mark {
  id: string;
  row: StudyRow;
  direction: Direction;
  r: number;
  x: number;
  y: number;
}

/** Radius by a fifth root of sample size: between a log scale, which makes a 400-person trial
 * look nearly as big as a 36,000-person one, and an area scale, which leaves every small study
 * the same speck. The chart scales it further to fit the panel. */
export function radius(n: number | null): number {
  if (n === null) return 3.5;
  return 1 + 1.25 * Math.max(10, n) ** 0.2;
}

/** Fill opacity by design weight: a trial is solid, a case series nearly an outline, and a
 * design without a weight only an outline. */
export function solidity(weight: number | null): number {
  if (weight === null) return 0;
  if (weight >= 3) return 1;
  if (weight >= 2) return 0.55;
  if (weight >= 1) return 0.25;
  return 0.08;
}

/** The designs present, strongest first, for the legend. */
export function designsPresent(rows: StudyRow[]): { design: Design; weight: number | null }[] {
  const seen = new Map<Design, number | null>();
  for (const r of rows) seen.set(r.study.design, r.study.weight);
  return [...seen.entries()]
    .map(([design, weight]) => ({ design, weight }))
    .sort((a, b) => (b.weight ?? -1) - (a.weight ?? -1) || (a.design < b.design ? -1 : 1));
}

export interface ZoneStats {
  studies: number;
  /** The direction's share of the design weight, as the strip computes it; null with none. */
  share: number | null;
}

/** Counts and design-weighted shares over the counted studies of the designs picked (all when
 * none is picked), so the band labels follow the legend. */
export function zoneStats(rows: StudyRow[], only: ReadonlySet<Design>): Record<Direction, ZoneStats> {
  const kept = rows.filter((r) => r.notCounted === null && (only.size === 0 || only.has(r.study.design)));
  const total = kept.reduce((sum, r) => sum + (r.study.weight ?? 0), 0);
  const stats = (d: Direction): ZoneStats => {
    const here = kept.filter((r) => r.finding.direction === d);
    const weight = here.reduce((sum, r) => sum + (r.study.weight ?? 0), 0);
    return { studies: here.length, share: total > 0 ? weight / total : null };
  };
  return { benefit: stats("benefit"), none: stats("none"), harm: stats("harm") };
}

// The literature thins out before 2010, so those years share one section as wide as the two-year
// steps after it, rather than taking half the axis for a handful of studies. Every outcome uses
// the same axis, from that section to the year the abstracts were retrieved, so switching
// outcomes keeps each year in place.
export const CUT = 2010;
export const STEP = 2;
/** The least room between two year labels' starts. */
const LABEL_ROOM = 34;

export interface Tick {
  at: number;
  label: string | null;
  /** A section boundary gets a gridline; the pre-2010 label sits in its section's middle. */
  boundary: boolean;
}

export interface Axis {
  left: number;
  right: number;
  /** The section a year falls in: a key shared by its studies, its centre and its width. */
  section: (year: number) => { key: number; centre: number; width: number };
  ticks: Tick[];
}

export function yearAxis(last: number, left: number, right: number): Axis {
  const start = CUT - STEP;
  const slots = Math.max(last, CUT) - start + 1;
  const x = (slot: number) => left + (slot / slots) * (right - left);
  const section = (year: number) =>
    year < CUT
      ? { key: 0, centre: x(STEP / 2), width: x(STEP) - x(0) }
      : { key: year, centre: x(year - start + 0.5), width: x(1) - x(0) };
  // Every two-year boundary gets a gridline; labels thin out to every four years or more when
  // the axis is too narrow to name each one.
  const every = STEP * Math.max(1, Math.ceil(LABEL_ROOM / (x(STEP) - x(0))));
  const ticks: Tick[] = [
    { at: x(0), label: null, boundary: true },
    { at: x(STEP / 2), label: `<${CUT}`, boundary: false },
  ];
  // "2010" starts where the pre-2010 label ends, so it gives way when that section is narrow.
  const crowded = x(STEP) - x(0) < LABEL_ROOM;
  for (let year = CUT; year <= last; year += STEP) {
    const named = (year - CUT) % every === 0 && !(crowded && year === CUT);
    ticks.push({ at: x(year - start), label: named ? String(year) : null, boundary: true });
  }
  return { left, right, section, ticks };
}

const GAP = 1.5;

/** Beeswarm: each mark keeps its x and takes the free y nearest the line, biggest first. The
 * order is fixed by size then id, so the same data always draws the same picture. */
export function swarm(marks: Mark[]): void {
  const placed: Mark[] = [];
  for (const m of [...marks].sort((a, b) => b.r - a.r || (a.id < b.id ? -1 : 1))) {
    const near = (y: number) =>
      placed.some((p) => {
        const d = m.r + p.r + GAP;
        return Math.abs(p.x - m.x) < d && (p.x - m.x) ** 2 + (p.y - y) ** 2 < d * d;
      });
    let y = 0;
    for (let step = 0; near(y); step += 1) {
      y = (step % 2 === 0 ? 1 : -1) * Math.ceil((step + 1) / 2);
    }
    m.y = y;
    placed.push(m);
  }
}

/** The largest factor in [low, high] for which fits(k) holds, by bisection. */
export function largestFit(fits: (k: number) => boolean, low: number, high: number): number {
  if (fits(high)) return high;
  if (!fits(low)) return low;
  for (let i = 0; i < 12; i += 1) {
    const mid = (low + high) / 2;
    if (fits(mid)) low = mid;
    else high = mid;
  }
  return low;
}

export const AXIS = 26;
const PAD = 10;
const FOOT = 4;
const EMPTY_BAND = 38;
/** A band with dots always has room for its name and both stat lines, so picking designs in
 * the legend never moves it. */
const MIN_BAND = 66;
/** In the compact layout each band's label is one line across its top. */
export const COMPACT_HEAD = 22;
/** Below this width the label column gives way to labels across each band's top. */
export const COMPACT_BELOW = 560;
const LABEL_COLUMN = 160;
const EDGE = 14;

export interface Band {
  direction: Direction;
  marks: Mark[];
  top: number;
  height: number;
  /** Where the band's swarm is centred. */
  mid: number;
}

export interface BandLayout {
  compact: boolean;
  axis: Axis;
  /** The dot scale, shared by every outcome. */
  k: number;
  bands: Band[];
  height: number;
}

/** One outcome's bands at dot scale k, each as tall as its swarm and its labels need. */
function pack(rows: StudyRow[], axis: Axis, k: number, compact: boolean) {
  const head = compact ? COMPACT_HEAD : 0;
  return DIRECTIONS.map((direction) => {
    const marks: Mark[] = rows
      .filter((r) => r.finding.direction === direction)
      .map((row) => ({ id: row.finding.id, row, direction, r: radius(row.study.sample_size) * k, x: 0, y: 0 }));
    // Studies share only a year, so each section's dots spread evenly across it, oldest first,
    // rather than stacking in one stick; each dot stays inside its own section.
    const bySection = new Map<number, Mark[]>();
    for (const m of marks) {
      const { key } = axis.section(m.row.study.year);
      bySection.set(key, [...(bySection.get(key) ?? []), m]);
    }
    for (const group of bySection.values()) {
      group.sort((a, b) => a.row.study.year - b.row.study.year || (a.id < b.id ? -1 : 1));
      group.forEach((m, i) => {
        const { centre, width } = axis.section(m.row.study.year);
        const x = centre + (group.length === 1 ? 0 : ((i + 0.5) / group.length - 0.5) * width * 0.9);
        m.x = Math.min(axis.right - m.r, Math.max(axis.left + m.r, x));
      });
    }
    swarm(marks);
    const reach = Math.max(0, ...marks.map((m) => Math.abs(m.y) + m.r));
    const height =
      marks.length === 0 ? EMPTY_BAND : head + Math.max(compact ? 0 : MIN_BAND, 2 * reach + 2 * PAD);
    return { direction, marks, height };
  });
}

function total(packed: { height: number }[]): number {
  return AXIS + packed.reduce((sum, b) => sum + b.height, 0) + FOOT;
}

/** The chart for `rows` in a `width` by `room` panel. The dot scale is the largest at which the
 * busiest of `everyOutcome` still fits, so a dot's size means the same sample size on every
 * outcome; the height that leaves over goes to the bands with dots, by their height. A panel
 * too short for the smallest dots gets a chart taller than itself, to scroll. */
export function layoutBands(
  rows: StudyRow[],
  everyOutcome: StudyRow[][],
  through: number,
  width: number,
  room: number,
): BandLayout | null {
  const compact = width < COMPACT_BELOW;
  const left = compact ? EDGE : LABEL_COLUMN;
  if (width - EDGE <= left || room <= 0) return null;
  const last = Math.max(through, ...everyOutcome.flat().map((r) => r.study.year));
  const axis = yearAxis(last, left, width - EDGE);
  const k = Math.min(
    ...[rows, ...everyOutcome].map((r) => largestFit((f) => total(pack(r, axis, f, compact)) <= room, 0.3, 2.5)),
  );
  const packed = pack(rows, axis, k, compact);
  const spare = Math.max(0, room - total(packed));
  const filled = packed.filter((b) => b.marks.length > 0).reduce((sum, b) => sum + b.height, 0);
  const head = compact ? COMPACT_HEAD : 0;
  let top = AXIS;
  const bands = packed.map((b) => {
    const height = b.height + (b.marks.length > 0 && filled > 0 ? (spare * b.height) / filled : 0);
    const mid = b.marks.length === 0 ? top + height / 2 : top + head + (height - head) / 2;
    const band = { ...b, top, height, mid };
    top += height;
    return band;
  });
  return { compact, axis, k, bands, height: Math.max(room, top + FOOT) };
}

/** The order the arrow keys walk: band by band, oldest to newest within each. */
export function keyboardOrder(bands: Band[]): Mark[] {
  return bands.flatMap((b) => [...b.marks].sort((m, n) => m.x - n.x || m.y - n.y || (m.id < n.id ? -1 : 1)));
}
