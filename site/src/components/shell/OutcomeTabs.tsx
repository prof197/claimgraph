import { href } from "../../routes";
import type { Outcome } from "../../types/export";
import { studies } from "./labels";

/** Each outcome is its own route, so the tabs are links; the current one opens into the band
 * below it. */
export function OutcomeTabs({ outcomes, current }: { outcomes: Outcome[]; current: string | null }) {
  return (
    <nav aria-label="Outcomes" className="shell-tabs">
      <ul className="shell-tabs__list">
        {outcomes.map((o) => {
          const isCurrent = o.name === current;
          return (
            <li key={o.name}>
              <a
                href={href({ page: "question", outcome: o.name })}
                aria-current={isCurrent ? "page" : undefined}
                className={isCurrent ? "shell-tab shell-tab--current" : "shell-tab"}
              >
                <span className="shell-tab__name">{o.name}</span>
                <span className="shell-tab__count">{studies(o.summary.studies)}</span>
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
