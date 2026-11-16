// The outcome's findings around its hub: one dot per study, placed by direction, with the
// genuine contradictions between them. The list view is the accessible path; here the dots are
// still real buttons, one tab stop for the whole graph and arrow keys between dots.
import { type KeyboardEvent, useId, useMemo, useRef, useState } from "react";
import { studiesByKey } from "../../lib/data";
import type { Direction, Export, Outcome } from "../../types/export";
import {
  DESIGN_LABEL,
  DIRECTION_LABEL,
  formatCount,
  inSentence,
  isObservational,
  nShort,
  plural,
  studyLabel,
} from "../contradictions/labels";
import styles from "./GraphView.module.css";
import {
  CX,
  CY,
  HEIGHT,
  HUB_R,
  WIDTH,
  type GraphNode,
  hubLines,
  keyboardOrder,
  layoutGraph,
} from "./layout";

export interface GraphViewProps {
  data: Export;
  outcome: Outcome;
  /** The selected finding's node id, shown in the finding card. */
  selected: string | null;
  onSelect: (findingId: string) => void;
}

/** Past this many lines the picture is a hairball, so only the strongest pairs are drawn
 * until a study is selected. */
export const EDGE_LIMIT = 12;
/** Up to this many studies, every dot is named. */
export const LABEL_ALL = 8;

const SECTOR_LABEL: Record<Direction, { x: number; y: number; anchor: "start" | "end" | "middle" }> = {
  benefit: { x: 10, y: 18, anchor: "start" },
  none: { x: WIDTH - 10, y: 18, anchor: "end" },
  harm: { x: CX, y: HEIGHT - 8, anchor: "middle" },
};

/** Baselines for the hub's name lines, centred a little high to leave room for the count. */
function hubY(line: number, lines: number): number {
  return CY + 16 * line - 8 * (lines - 1) + 3;
}

function dotClass(node: GraphNode): string {
  return `${styles.dot} ${node.randomized ? styles.filled : styles.ring} ${styles[node.direction]}`;
}

/** A label pushed outward from the hub along the dot's angle, anchored away from it. */
function outwardLabel(node: GraphNode) {
  const distance = node.r + 8;
  const cos = Math.cos(node.angle);
  const anchor: "start" | "end" | "middle" = cos > 0.05 ? "start" : cos < -0.05 ? "end" : "middle";
  return {
    x: node.x + distance * cos,
    y: node.y + distance * Math.sin(node.angle) + 4,
    anchor,
  };
}

export function GraphView({ data, outcome, selected, onSelect }: GraphViewProps) {
  const layout = useMemo(() => layoutGraph(data, outcome), [data, outcome]);
  const studies = useMemo(() => studiesByKey(data), [data]);
  const order = useMemo(() => keyboardOrder(layout.nodes), [layout]);
  const [focusId, setFocusId] = useState<string | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const descId = useId();

  const selectedNode = useMemo(() => {
    if (selected === null) return undefined;
    const finding = data.graph.nodes.find((n) => n.kind === "finding" && n.id === selected);
    return finding?.kind === "finding" ? layout.byStudy.get(finding.study) : undefined;
  }, [data, layout, selected]);

  if (layout.nodes.length === 0) {
    return (
      <div className={styles.root}>
        <p className={styles.empty}>No findings on {inSentence(outcome.name)} to draw.</p>
      </div>
    );
  }

  const label = (node: GraphNode) => studyLabel(studies.get(node.study), node.study);
  const ownEdges = selectedNode
    ? layout.edges.filter((e) => e.source === selectedNode.id || e.target === selectedNode.id)
    : [];
  const faintEdges = layout.edges.filter((e) => !ownEdges.includes(e)).slice(0, EDGE_LIMIT);
  const partners = ownEdges.flatMap((e) => {
    const other = e.source === selectedNode?.id ? e.target : e.source;
    const node = layout.nodes.find((n) => n.id === other);
    return node === undefined ? [] : [node];
  });
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  const pairCount = new Map<string, number>();
  for (const edge of layout.edges) {
    pairCount.set(edge.source, (pairCount.get(edge.source) ?? 0) + 1);
    pairCount.set(edge.target, (pairCount.get(edge.target) ?? 0) + 1);
  }
  const labelAll = layout.nodes.length <= LABEL_ALL;
  const tabStop =
    (focusId !== null && byId.has(focusId) ? focusId : undefined) ??
    selectedNode?.id ??
    order[0]?.id;
  const counts = new Map<Direction, number>();
  for (const node of layout.nodes) {
    counts.set(node.direction, (counts.get(node.direction) ?? 0) + 1);
  }
  const subgroupOnly = layout.nodes.filter((n) => !n.headline).length;
  const anyRing = layout.nodes.some((n) => !n.randomized);
  const ringsObservational = layout.nodes
    .filter((n) => !n.randomized)
    .every((n) => {
      const design = studies.get(n.study)?.design;
      return design !== undefined && isObservational(design);
    });

  const move = (event: KeyboardEvent<HTMLButtonElement>, node: GraphNode) => {
    const at = order.findIndex((n) => n.id === node.id);
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

  const accessibleName = (node: GraphNode) => {
    const study = studies.get(node.study);
    const parts = [
      DIRECTION_LABEL[node.direction],
      study === undefined ? "design not known" : DESIGN_LABEL[study.design].toLowerCase(),
      nShort(study?.sample_size ?? null),
    ];
    if (!node.headline) parts.push("subgroup results only, not counted");
    const k = pairCount.get(node.id) ?? 0;
    if (k > 0) parts.push(plural(k, "contradiction", "contradictions"));
    return `${label(node)}: ${parts.join(", ")}`;
  };

  const hub = hubLines(outcome.name);
  const sectors = (["benefit", "none", "harm"] as const).filter((d) => (counts.get(d) ?? 0) > 0);
  const where: Record<Direction, string> = {
    benefit: "benefit on the left",
    none: "no effect on the right",
    harm: "harm at the bottom",
  };

  return (
    <div className={styles.root}>
      <p id={descId} className={styles.visuallyHidden}>
        {plural(layout.nodes.length, "study", "studies")} on {inSentence(outcome.name)} arranged
        around the outcome:{" "}
        {sectors.map((d) => `${formatCount(counts.get(d) ?? 0)} ${where[d]}`).join(", ")}, with{" "}
        {plural(layout.edges.length, "contradiction", "contradictions")}. Arrow keys move between
        studies; Enter selects one.
      </p>
      <div className={styles.canvas}>
        <svg
          className={styles.svg}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="group"
          aria-label={`Findings on ${outcome.name}, as a graph`}
          aria-describedby={descId}
          data-testid="graph"
        >
          <g aria-hidden="true">
            {layout.nodes.map((node) =>
              node.spoke === null ? null : (
                <line
                  key={`spoke-${node.id}`}
                  className={styles.spoke}
                  x1={node.spoke.x}
                  y1={node.spoke.y}
                  x2={node.x}
                  y2={node.y}
                />
              ),
            )}
            {faintEdges.map((edge) => (
              <path key={edge.pairId} className={styles.edgeFaint} d={edge.d} data-pair={edge.pairId} />
            ))}
            {ownEdges.map((edge) => (
              <path key={edge.pairId} className={styles.edgeStrong} d={edge.d} data-pair={edge.pairId} />
            ))}
            {layout.nodes.map((node) => (
              <circle
                key={node.id}
                className={dotClass(node)}
                cx={node.x}
                cy={node.y}
                r={node.r}
                style={{ strokeWidth: Math.min(2, Math.max(0.75, node.r / 3)) }}
                data-node={node.id}
              />
            ))}
            <circle className={styles.hub} cx={CX} cy={CY} r={HUB_R} />
            {hub.map((line, i) => (
              <text key={line} className={styles.hubName} x={CX} y={hubY(i, hub.length)}>
                {line}
              </text>
            ))}
            <text className={styles.hubCount} x={CX} y={hubY(hub.length - 1, hub.length) + 18}>
              {plural(outcome.summary.studies, "study", "studies")}
            </text>
            {sectors.map((d) => (
              <text
                key={d}
                className={styles.sectorLabel}
                x={SECTOR_LABEL[d].x}
                y={SECTOR_LABEL[d].y}
                textAnchor={SECTOR_LABEL[d].anchor}
              >
                {DIRECTION_LABEL[d]} · {formatCount(counts.get(d) ?? 0)}
              </text>
            ))}
            {labelAll &&
              layout.nodes.map((node) => {
                const at = outwardLabel(node);
                return (
                  <text
                    key={`label-${node.id}`}
                    className={styles.label}
                    x={at.x}
                    y={at.y}
                    textAnchor={at.anchor}
                  >
                    {label(node)}
                  </text>
                );
              })}
            {partners.map((node) => (
              <circle key={`partner-${node.id}`} className={styles.partnerMark} cx={node.x} cy={node.y} r={node.r + 5} />
            ))}
            {!labelAll &&
              partners.length <= 4 &&
              partners.map((node) => (
                <text
                  key={`partner-label-${node.id}`}
                  className={styles.label}
                  x={node.x}
                  y={node.y - node.r - 11}
                  textAnchor="middle"
                >
                  {label(node)}
                </text>
              ))}
            {selectedNode && (
              <>
                <circle
                  className={styles.mark}
                  cx={selectedNode.x}
                  cy={selectedNode.y}
                  r={selectedNode.r + 6}
                  data-selected={selectedNode.id}
                />
                {!labelAll && (
                  <text
                    className={styles.label}
                    x={selectedNode.x}
                    y={selectedNode.y - selectedNode.r - 12}
                    textAnchor="middle"
                  >
                    {label(selectedNode)}
                  </text>
                )}
              </>
            )}
          </g>
          {order.map((node) => {
            const hit = Math.max(2 * node.r + 6, 14);
            return (
              <foreignObject
                key={`hit-${node.id}`}
                className={styles.hit}
                x={node.x - hit / 2}
                y={node.y - hit / 2}
                width={hit}
                height={hit}
              >
                <button
                  ref={(el) => {
                    if (el === null) buttons.current.delete(node.id);
                    else buttons.current.set(node.id, el);
                  }}
                  type="button"
                  className={styles.node}
                  tabIndex={node.id === tabStop ? 0 : -1}
                  aria-pressed={node.id === selectedNode?.id}
                  aria-label={accessibleName(node)}
                  title={label(node)}
                  onClick={() => onSelect(node.id)}
                  onFocus={() => setFocusId(node.id)}
                  onKeyDown={(event) => move(event, node)}
                />
              </foreignObject>
            );
          })}
        </svg>
      </div>
      <ul className={styles.legend} aria-label="Legend">
        {(["benefit", "none", "harm"] as const).map((d) => (
          <li key={d}>
            <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
              <circle className={`${styles.filled} ${styles[d]}`} cx="6" cy="6" r="5" strokeWidth="0" />
            </svg>
            {DIRECTION_LABEL[d]}
          </li>
        ))}
        <li>
          <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
            <circle cx="6" cy="6" r="5" style={{ fill: "var(--color-text)" }} />
          </svg>
          Filled: randomized trial
        </li>
        {anyRing && (
          <li>
            <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
              <circle
                cx="6"
                cy="6"
                r="4.5"
                style={{ fill: "var(--color-surface)", stroke: "var(--color-text)", strokeWidth: 1.5 }}
              />
            </svg>
            {ringsObservational ? "Ring: observational" : "Ring: not randomized"}
          </li>
        )}
        <li>Larger dot: larger study</li>
        {layout.edges.length > 0 && (
          <li>
            <svg aria-hidden="true" width="24" height="12" viewBox="0 0 24 12">
              <line className={styles.edgeFaint} x1="0" y1="6" x2="24" y2="6" style={{ strokeWidth: 1.5 }} />
            </svg>
            Contradiction
          </li>
        )}
        {subgroupOnly > 0 && <li>No line to the hub: subgroup results only, not counted</li>}
      </ul>
      {layout.edges.length > EDGE_LIMIT && (
        <p className={styles.note}>
          Lines show the {EDGE_LIMIT} strongest of {formatCount(layout.edges.length)} contradictions.
          Select a study to see all of its own.
        </p>
      )}
    </div>
  );
}
