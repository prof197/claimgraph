// Placeholder layout: the question page's shell replaces it, placing the graph and the
// contradiction panel through the props they declare.
import { ContradictionPanel } from "../contradictions/ContradictionPanel";
import { GraphView } from "../graph/GraphView";
import { useState } from "react";
import { outcomeNamed } from "../../lib/data";
import { href } from "../../routes";
import type { Export } from "../../types/export";

export function QuestionPage({ data, outcome }: { data: Export; outcome: string | null }) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = outcomeNamed(data, outcome);
  return (
    <main>
      <h1>{data.question.title}</h1>
      <nav aria-label="Outcomes">
        <ul>
          {data.outcomes.map((o) => (
            <li key={o.name}>
              <a href={href({ page: "question", outcome: o.name })}>{o.name}</a>
            </li>
          ))}
        </ul>
      </nav>
      {current === undefined ? (
        <p>No outcome named {outcome}.</p>
      ) : (
        <>
          <GraphView data={data} outcome={current} selected={selected} onSelect={setSelected} />
          <ContradictionPanel data={data} outcome={current} />
        </>
      )}
      <footer>Research navigation demo, not medical advice.</footer>
    </main>
  );
}
