import { useMemo, useState } from "react";
import { findingsOn, outcomeNamed } from "../../lib/data";
import { href } from "../../routes";
import type { Export, FindingNode, Outcome } from "../../types/export";
import { ContradictionPanel } from "../contradictions/ContradictionPanel";
import { BandChart } from "../bands/BandChart";
import { FindingCard } from "./FindingCard";
import { FindingList } from "./FindingList";
import { PageFooter, PageHeader } from "./Frame";
import { inSentence, longDate, number, studyRows } from "./labels";
import { Method, Reviews } from "./Method";
import { OutcomeTabs } from "./OutcomeTabs";
import { SummaryStrip } from "./SummaryStrip";
import "./shell.css";

export type View = "graph" | "list";

/** The list is the accessible path and the default on narrow screens. */
export function initialView(): View {
  const narrow =
    typeof window.matchMedia === "function" && window.matchMedia("(max-width: 719.98px)").matches;
  return narrow ? "list" : "graph";
}

export function QuestionPage({ data, outcome }: { data: Export; outcome: string | null }) {
  const current = outcomeNamed(data, outcome);
  const [view, setView] = useState<View>(initialView);
  // The reader's pick per outcome, so switching tabs and back keeps it.
  const [picked, setPicked] = useState<Record<string, string>>({});
  const here = href({ page: "question", outcome });
  const pick = (name: string, findingId: string) => setPicked((p) => ({ ...p, [name]: findingId }));

  return (
    <>
      <PageHeader here={here} />
      <main className="shell-main">
        <div className="shell-wrap shell-top">
          <section className="shell-hero" aria-labelledby="question-h">
            <p className="shell-eyebrow shell-eyebrow--large">Research question</p>
            <h1 id="question-h" className="shell-h1">
              {data.question.title}
            </h1>
            <p className="shell-hero__lede">
              Findings extracted from {number(data.question.abstracts)} PubMed abstracts, retrieved{" "}
              {longDate(data.question.retrieved)}. Every finding quotes the sentence it came from
              and links to its abstract.
            </p>
          </section>
          <OutcomeTabs outcomes={data.outcomes} current={current?.name ?? null} />
        </div>
        <div className="shell-band">
          <div className="shell-wrap shell-band__inner">
            {current === undefined ? (
              <p className="shell-card shell-missing" role="status">
                No outcome named {outcome}. Pick one of the outcomes above.
              </p>
            ) : (
              <OutcomeBody
                data={data}
                outcome={current}
                view={view}
                onView={setView}
                selected={picked[current.name]}
                onSelect={(id) => pick(current.name, id)}
                onAlso={pick}
              />
            )}
            <Reviews reviews={data.reviews} />
            <Method data={data} />
          </div>
        </div>
      </main>
      <PageFooter />
    </>
  );
}

interface OutcomeBodyProps {
  data: Export;
  outcome: Outcome;
  view: View;
  onView: (view: View) => void;
  selected: string | undefined;
  onSelect: (findingId: string) => void;
  onAlso: (outcome: string, findingId: string) => void;
}

function OutcomeBody({ data, outcome, view, onView, selected, onSelect, onAlso }: OutcomeBodyProps) {
  const rows = studyRows(data, outcome);
  const findings = findingsOn(data, outcome);
  const everyOutcome = useMemo(() => data.outcomes.map((o) => studyRows(data, o)), [data]);
  const finding: FindingNode | undefined = findings.find((f) => f.id === selected);
  const name = inSentence(outcome.name);

  return (
    <>
      <SummaryStrip data={data} outcome={outcome} />
      <section className="shell-section" aria-labelledby="graph-h">
        <div className="shell-section__intro">
          <h2 id="graph-h" className="shell-h2">
            How the findings line up
          </h2>
          <p className="shell-intro">
            {view === "graph"
              ? `Each dot is one study of ${name}, in the band for what it found, placed by publication year. Select a dot to see its finding.`
              : `Each row is one study's finding on ${name}, grouped by what it found. Select a study to see its finding.`}
          </p>
        </div>
        <div className="shell-split">
          <div className="shell-card shell-figure">
            <div className="shell-figure__bar">
              <p className="shell-small shell-muted">
                {`${number(rows.length)} ${rows.length === 1 ? "study" : "studies"} on ${name}`}
              </p>
              <div role="group" aria-label="Show the findings as" className="shell-toggle">
                <button type="button" aria-pressed={view === "graph"} onClick={() => onView("graph")}>
                  Graph
                </button>
                <button type="button" aria-pressed={view === "list"} onClick={() => onView("list")}>
                  List
                </button>
              </div>
            </div>
            <div className="shell-figure__view">
              {view === "graph" ? (
                <BandChart
                  outcome={outcome}
                  rows={rows}
                  everyOutcome={everyOutcome}
                  through={Number(data.question.retrieved.slice(0, 4))}
                  selected={finding?.id ?? null}
                  onSelect={onSelect}
                />
              ) : (
                <FindingList
                  outcome={outcome}
                  rows={rows}
                  selected={finding?.id ?? null}
                  onSelect={onSelect}
                />
              )}
            </div>
          </div>
          <FindingCard data={data} outcome={outcome} finding={finding} onAlso={onAlso} />
        </div>
      </section>
      <ContradictionPanel data={data} outcome={outcome} />
    </>
  );
}
