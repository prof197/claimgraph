// Placeholder: the contradiction panel replaces it, keeping these props.
import { contradictionsOn } from "../../lib/data";
import { href } from "../../routes";
import type { Export, Outcome } from "../../types/export";

export interface ContradictionPanelProps {
  data: Export;
  outcome: Outcome;
}

export function ContradictionPanel({ data, outcome }: ContradictionPanelProps) {
  const pairs = contradictionsOn(data, outcome);
  return (
    <section aria-label="Where studies disagree">
      <h2>Where studies disagree</h2>
      <ul>
        {pairs.map((pair) => (
          <li key={pair.pair_id}>
            <a href={href({ page: "pair", pairId: pair.pair_id })}>{pair.detail}</a>
          </li>
        ))}
      </ul>
    </section>
  );
}
