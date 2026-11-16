// Placeholder: the graph view replaces it, keeping these props.
import { findingsOn } from "../../lib/data";
import type { Export, Outcome } from "../../types/export";

export interface GraphViewProps {
  data: Export;
  outcome: Outcome;
  /** The selected finding's node id, shown in the finding card. */
  selected: string | null;
  onSelect: (findingId: string) => void;
}

export function GraphView({ data, outcome }: GraphViewProps) {
  const findings = findingsOn(data, outcome).filter((f) => f.headline);
  return (
    <section aria-label={`Findings on ${outcome.name}`}>
      <p>
        {findings.length} findings on {outcome.name}
      </p>
    </section>
  );
}
