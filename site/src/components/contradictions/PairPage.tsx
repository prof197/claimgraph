// Placeholder: the pair page replaces it, keeping these props.
import { href } from "../../routes";
import type { Export } from "../../types/export";

export interface PairPageProps {
  data: Export;
  pairId: string;
}

export function PairPage({ data, pairId }: PairPageProps) {
  const pair = data.graph.edges.find((e) => e.kind === "contradiction" && e.pair_id === pairId);
  return (
    <main>
      <a href={href({ page: "question", outcome: null })}>Back to all contradictions</a>
      <h1>{pair === undefined ? "No such pair" : `Contradiction · ${pairId.split("|")[0]}`}</h1>
    </main>
  );
}
