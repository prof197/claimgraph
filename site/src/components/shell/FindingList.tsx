import { useState } from "react";
import type { Outcome } from "../../types/export";
import {
  DIRECTION_LABEL,
  DIRECTIONS,
  NOT_COUNTED_LABEL,
  type StudyRow,
  designShort,
  inSentence,
  number,
  studies,
  studyLabel,
} from "./labels";

/** Rows a long group shows before "Show all". */
const FOLDED = 5;

export interface FindingListProps {
  outcome: Outcome;
  rows: StudyRow[];
  selected: string | null;
  onSelect: (findingId: string) => void;
}

/** The accessible path through the findings: a table grouped by direction. */
export function FindingList({ outcome, rows, selected, onSelect }: FindingListProps) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  return (
    <div
      className="shell-list"
      role="region"
      aria-label={`Findings on ${inSentence(outcome.name)}, as a list`}
    >
      <table className="shell-table">
        <thead>
          <tr>
            <th scope="col">Study</th>
            <th scope="col">Measured as</th>
            <th scope="col">Design</th>
            <th scope="col" className="shell-num">
              n
            </th>
            <th scope="col">Year</th>
          </tr>
        </thead>
        {DIRECTIONS.map((direction) => {
          const group = rows.filter((r) => r.finding.direction === direction);
          if (group.length === 0) {
            return null;
          }
          const counted = group.filter((r) => r.notCounted === null).length;
          const extra = group.length - counted;
          const key = `${outcome.name}|${direction}`;
          const folded = group.length > FOLDED + 1 && !open.has(key);
          const shown = folded ? group.slice(0, FOLDED) : group;
          return (
            <tbody key={direction}>
              <tr>
                <th colSpan={5} scope="rowgroup" className={`shell-group shell-group--${direction}`}>
                  {DIRECTION_LABEL[direction]} · {studies(counted)}
                  {extra > 0 && `, plus ${extra} not counted`}
                </th>
              </tr>
              {shown.map((row) => {
                const isSelected = row.finding.id === selected;
                return (
                  <tr key={row.finding.id} className={isSelected ? "shell-row--selected" : undefined}>
                    <td className="shell-cell--button">
                      <button
                        type="button"
                        className="shell-rowbutton"
                        aria-pressed={isSelected}
                        onClick={() => onSelect(row.finding.id)}
                      >
                        {studyLabel(row.study)}
                      </button>
                      {row.notCounted !== null && (
                        <span className="shell-tag">Not counted: {NOT_COUNTED_LABEL[row.notCounted]}</span>
                      )}
                    </td>
                    <td>{row.finding.outcome_reported}</td>
                    <td>{designShort(row.study.design)}</td>
                    <td className="shell-num">
                      {row.study.sample_size === null ? (
                        <span className="shell-muted">Not reported</span>
                      ) : (
                        number(row.study.sample_size)
                      )}
                    </td>
                    <td>{row.study.year}</td>
                  </tr>
                );
              })}
              {folded && (
                <tr>
                  <td colSpan={5} className="shell-cell--button">
                    <button
                      type="button"
                      className="shell-showall"
                      onClick={() => setOpen(new Set(open).add(key))}
                    >
                      Show all {group.length}
                    </button>
                  </td>
                </tr>
              )}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
