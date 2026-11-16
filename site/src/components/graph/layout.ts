// The graph's geometry, computed from the export alone: no randomness and no simulation, so the
// same export always draws the same picture. D3 supplies the size scale and the curve paths.
import { pathRound, scaleSqrt } from "d3";
import { findingsOn, studiesByKey } from "../../lib/data";
import type { ContradictionEdge, Direction, Export, FindingNode, Outcome } from "../../types/export";
import { orderedPairs } from "../contradictions/pairs";

export const WIDTH = 760;
export const HEIGHT = 520;
export const CX = 380;
export const CY = 260;
export const HUB_R = 46;

// Room kept clear at the top and bottom for the sector labels.
const EDGE_Y = 26;
const EDGE_X = 10;
const R_MIN = 4;
const R_MAX = 15;
// How far a contradiction curve bows above or below the hub, so it never crosses it.
const BOW = 230;
// The canvas's rings: the first well clear of the hub, then evenly spaced.
const INNER_RING = 124;
const RING_STEP = 54;
// Dots per ring a sector aims for before it opens another ring.
const PER_RING = 8;
// Alternate dots sit this far in or out of their ring, so rings read as clusters, not tracks.
const ZIGZAG = 7;

/** Benefit sits left of the hub, no effect right and harm below, so position carries
 * direction as well as colour. Angles are SVG's: 0 points right, a quarter turn points down. */
const SECTOR_CENTER: Record<Direction, number> = { none: 0, harm: Math.PI / 2, benefit: Math.PI };
const SECTOR_ORDER: readonly Direction[] = ["benefit", "none", "harm"];

export interface GraphNode {
  /** The finding drawn for its study: its headline finding, or its first subgroup finding. */
  id: string;
  study: string;
  direction: Direction;
  /** False for a study with only subgroup findings here: drawn, not counted, no spoke. */
  headline: boolean;
  randomized: boolean;
  r: number;
  x: number;
  y: number;
  angle: number;
  /** Where the spoke leaves the hub; null when the study has no headline finding. */
  spoke: { x: number; y: number } | null;
}

export interface GraphEdge {
  pairId: string;
  source: string;
  target: string;
  d: string;
}

export interface GraphLayout {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Nodes by study key, so a selected finding of any kind finds its study's dot. */
  byStudy: Map<string, GraphNode>;
}

function round1(v: number): number {
  return Math.round(v * 10) / 10;
}

function suffix(id: string): number {
  const n = Number(id.slice(id.lastIndexOf("-") + 1));
  return Number.isFinite(n) ? n : 0;
}

function byReportedOrder(a: FindingNode, b: FindingNode): number {
  if (a.study !== b.study) return a.study < b.study ? -1 : 1;
  if (suffix(a.id) !== suffix(b.id)) return suffix(a.id) - suffix(b.id);
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** One finding per study: the headline one, or for a subgroup-only study its first finding. */
export function drawnFindings(data: Export, outcome: Outcome): FindingNode[] {
  const chosen = new Map<string, FindingNode>();
  for (const finding of findingsOn(data, outcome).sort(byReportedOrder)) {
    const current = chosen.get(finding.study);
    if (current === undefined || (finding.headline && !current.headline)) {
      chosen.set(finding.study, finding);
    }
  }
  return [...chosen.values()];
}

/** Sector widths in degrees grow with the sector's share of studies. Harm sits at the
 * bottom between the other two, so its range is narrower; the bounds keep them apart. */
function sectorSpan(direction: Direction, share: number): number {
  const deg = direction === "harm" ? 24 + 36 * share : 60 + 80 * share;
  return (deg * Math.PI) / 180;
}

interface Sized {
  finding: FindingNode;
  base: number;
  weight: number;
  year: number;
}

interface Placed {
  finding: FindingNode;
  r: number;
  ring: number;
  angle: number;
}

/** The half-angle of a ring that stays inside the drawing, or null once the ring would
 * leave it. Left and right sectors narrow as rings grow, so they fill a lens. */
function usableHalf(direction: Direction, ringR: number, nodeR: number, span: number): number | null {
  const yMax = CY - EDGE_Y - nodeR;
  const xMax = CX - EDGE_X - nodeR;
  if (direction === "harm") {
    return ringR > yMax ? null : span / 2;
  }
  if (ringR > xMax) {
    return null;
  }
  return Math.min(span / 2, Math.asin(Math.min(1, yMax / ringR)));
}

interface Ring {
  r: number;
  half: number;
  /** Arc length available at the ring's inner edge, where dots sit closest together. */
  arc: number;
}

/** Shares out dots between rings in proportion to their arc length, largest dots inward.
 * Returns null when a ring would overflow. */
function distribute<T extends { r: number }>(items: T[], rings: Ring[], gap: number): T[][] | null {
  const total = rings.reduce((sum, ring) => sum + ring.arc, 0);
  const exact = rings.map((ring) => (items.length * ring.arc) / total);
  const quota = exact.map(Math.floor);
  // Largest remainders take the leftover dots; ties go to the inner ring.
  const order = exact.map((v, i) => ({ i, rest: v - Math.floor(v) })).sort((a, b) => b.rest - a.rest || a.i - b.i);
  for (let left = items.length - quota.reduce((a, b) => a + b, 0), k = 0; left > 0; left -= 1, k += 1) {
    const at = order[k % order.length]?.i ?? 0;
    quota[at] = (quota[at] ?? 0) + 1;
  }
  const groups: T[][] = [];
  let next = 0;
  for (const [i, ring] of rings.entries()) {
    const group = items.slice(next, next + (quota[i] ?? 0));
    next += group.length;
    const used = group.reduce((sum, item) => sum + 2 * item.r + gap, 0);
    if (used > ring.arc) return null;
    groups.push(group);
  }
  return groups;
}

/** Packs one sector's dots into rings around the hub, largest studies nearest it. Returns
 * null when they do not fit at this scale. */
function packSector(
  direction: Direction,
  items: Sized[],
  span: number,
  scale: number,
  force: boolean,
): Placed[] | null {
  const nodeGap = Math.max(1, 3 * scale);
  const ringGap = Math.max(1.5, 6 * scale);
  const zigzag = ZIGZAG * scale;
  const sorted = items
    .map((item) => ({ ...item, r: Math.max(1.5, item.base * scale) }))
    .sort(
      (a, b) =>
        b.r - a.r ||
        b.weight - a.weight ||
        (a.finding.id < b.finding.id ? -1 : a.finding.id > b.finding.id ? 1 : 0),
    );
  const maxR = sorted[0]?.r ?? R_MIN;
  const reach = maxR + zigzag;
  const step = Math.max(RING_STEP * scale, 2 * reach + ringGap);
  const rings: Ring[] = [];
  for (let r = Math.max(HUB_R + 12 + reach, INNER_RING * scale); ; r += step) {
    const half = usableHalf(direction, r + zigzag, maxR, span);
    if (half === null) break;
    // Keep the end dots inside the sector, and measure where dots crowd most.
    const inner = r - zigzag;
    rings.push({ r, half, arc: 2 * Math.max(0, half - reach / inner) * inner + 2 * maxR + nodeGap });
  }
  if (rings.length === 0) {
    if (!force) return null;
    rings.push({ r: INNER_RING * scale, half: span / 2, arc: span * INNER_RING * scale });
  }

  let groups: (typeof sorted)[] | null = null;
  const fewest = Math.min(rings.length, Math.max(1, Math.ceil(sorted.length / PER_RING)));
  for (let k = fewest; k <= rings.length && groups === null; k += 1) {
    groups = distribute(sorted, rings.slice(0, k), nodeGap);
  }
  if (groups === null) {
    if (!force) return null;
    groups = rings.map(() => [] as typeof sorted);
    sorted.forEach((item, i) => groups?.[i % rings.length]?.push(item));
  }

  const placed: Placed[] = [];
  for (const [k, group] of groups.entries()) {
    const ring = rings[k];
    if (ring === undefined || group.length === 0) continue;
    // Around the ring, earlier studies come first, so angle reads as time within a ring.
    const ordered = [...group].sort(
      (a, b) =>
        a.year - b.year || (a.finding.id < b.finding.id ? -1 : a.finding.id > b.finding.id ? 1 : 0),
    );
    const inner = ring.r - zigzag;
    const reachAngle = reach / inner;
    const start = SECTOR_CENTER[direction] - ring.half + reachAngle;
    const width = Math.max(0, 2 * ring.half - 2 * reachAngle);
    // Spread across the whole sector like the canvas when the dots allow it; otherwise give
    // each dot a slot as wide as it needs.
    const even = ordered.map((_, i) => (ordered.length === 1 ? SECTOR_CENTER[direction] : start + (width * i) / (ordered.length - 1)));
    const evenFits = ordered.every((item, i) => {
      const prev = ordered[i - 1];
      const a = even[i - 1];
      const b = even[i];
      return prev === undefined || a === undefined || b === undefined || (b - a) * inner >= item.r + prev.r + nodeGap;
    });
    let along = 0;
    const used = ordered.reduce((sum, item) => sum + 2 * item.r + nodeGap, 0);
    const extra = Math.max(0, width * inner + 2 * maxR + nodeGap - used) / ordered.length;
    for (const [i, item] of ordered.entries()) {
      const slot = 2 * item.r + nodeGap + extra;
      const angle = evenFits
        ? (even[i] ?? start)
        : start - reachAngle + (along + slot / 2) / inner;
      along += slot;
      const offset = ordered.length < 3 ? 0 : i % 2 === 0 ? -zigzag : zigzag;
      placed.push({ finding: item.finding, r: item.r, ring: ring.r + offset, angle });
    }
  }
  return placed;
}

function curve(p: GraphNode, q: GraphNode): string {
  const up = (p.y + q.y) / 2 <= CY;
  const path = pathRound(1);
  path.moveTo(p.x, p.y);
  path.quadraticCurveTo((p.x + q.x) / 2, up ? CY - BOW : CY + BOW, q.x, q.y);
  return path.toString();
}

export function layoutGraph(data: Export, outcome: Outcome): GraphLayout {
  const studies = studiesByKey(data);
  const findings = drawnFindings(data, outcome);
  const sizes = findings.map((f) => studies.get(f.study)?.sample_size ?? 0);
  const radius = scaleSqrt()
    .domain([0, Math.max(1, ...sizes)])
    .range([R_MIN, R_MAX])
    .clamp(true);

  const bySector = new Map<Direction, Sized[]>(SECTOR_ORDER.map((d) => [d, []]));
  for (const finding of findings) {
    const study = studies.get(finding.study);
    const n = study?.sample_size ?? null;
    bySector.get(finding.direction)?.push({
      finding,
      base: n === null ? R_MIN : radius(n),
      weight: study?.weight ?? 0,
      year: study?.year ?? 0,
    });
  }

  // Shrink every dot together until each sector fits; past the last step, draw what fits
  // least badly. Several hundred studies on one hub no longer read as a picture anyway,
  // and the list view carries them.
  let placed: Placed[] = [];
  let scale = 1;
  for (let attempt = 0; ; attempt += 1) {
    const force = attempt >= 24;
    const sectors = SECTOR_ORDER.map((direction) => {
      const items = bySector.get(direction) ?? [];
      const span = sectorSpan(direction, items.length / Math.max(1, findings.length));
      return items.length === 0 ? [] : packSector(direction, items, span, scale, force);
    });
    if (sectors.every((s) => s !== null)) {
      placed = sectors.flat() as Placed[];
      break;
    }
    scale *= 0.88;
  }

  const nodes = placed.map(({ finding, r, ring, angle }): GraphNode => {
    const design = studies.get(finding.study)?.design;
    return {
      id: finding.id,
      study: finding.study,
      direction: finding.direction,
      headline: finding.headline,
      randomized: design === "rct",
      r: round1(r),
      x: round1(CX + ring * Math.cos(angle)),
      y: round1(CY + ring * Math.sin(angle)),
      angle,
      spoke: finding.headline
        ? { x: round1(CX + (HUB_R + 2) * Math.cos(angle)), y: round1(CY + (HUB_R + 2) * Math.sin(angle)) }
        : null,
    };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = orderedPairs(data, outcome).flatMap((pair: ContradictionEdge): GraphEdge[] => {
    const p = byId.get(pair.source);
    const q = byId.get(pair.target);
    return p === undefined || q === undefined
      ? []
      : [{ pairId: pair.pair_id, source: p.id, target: q.id, d: curve(p, q) }];
  });
  return { nodes, edges, byStudy: new Map(nodes.map((n) => [n.study, n])) };
}

/** Keyboard order: sector by sector, inner rings first, then around each ring. */
export function keyboardOrder(nodes: GraphNode[]): GraphNode[] {
  const distance = (n: GraphNode) => Math.hypot(n.x - CX, n.y - CY);
  return [...nodes].sort(
    (a, b) =>
      SECTOR_ORDER.indexOf(a.direction) - SECTOR_ORDER.indexOf(b.direction) ||
      Math.round(distance(a)) - Math.round(distance(b)) ||
      a.angle - b.angle,
  );
}

/** The outcome name broken into at most three short lines for the hub. */
export function hubLines(name: string, width = 12): string[] {
  const lines: string[] = [];
  for (const word of name.split(/\s+/)) {
    const last = lines.at(-1);
    if (last !== undefined && last.length + 1 + word.length <= width) {
      lines[lines.length - 1] = `${last} ${word}`;
    } else {
      lines.push(word);
    }
  }
  if (lines.length > 3) {
    return [...lines.slice(0, 2), `${lines.slice(2).join(" ").slice(0, width - 1)}…`];
  }
  return lines;
}
