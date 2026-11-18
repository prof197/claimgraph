import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtures } from "../../fixtures";
import type { Export } from "../../types/export";
import { QuestionPage } from "./QuestionPage";

function outcomeNames(data: Export): string[] {
  return data.outcomes.map((o) => o.name);
}

const TEASER = "Select a study from the chart to see its details";

function detail(): HTMLElement {
  return screen.getByRole("complementary", { name: /./ });
}

async function showList() {
  await userEvent.click(screen.getByRole("button", { name: "List" }));
  return screen.getByRole("region", { name: /as a list$/ });
}

describe.each(Object.entries(fixtures))("the %s fixture", (_, data) => {
  it("renders the shell around every part of the page", () => {
    render(<QuestionPage data={data} outcome={null} />);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(data.question.title);
    const tabs = within(screen.getByRole("navigation", { name: "Outcomes" })).getAllByRole("link");
    expect(tabs.map((t) => t.querySelector(".shell-tab__name")?.textContent)).toEqual(
      outcomeNames(data),
    );
    expect(tabs[0]?.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("region", { name: /^Summary for / })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "How the findings line up" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Reviews and meta-analyses" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "How this page was made" })).toBeTruthy();
    expect(screen.getByRole("contentinfo").textContent).toContain(
      "Research navigation demo, not medical advice.",
    );
  });

  it("invites a pick until one is made, then shows the finding's verbatim quote", async () => {
    render(<QuestionPage data={data} outcome={null} />);
    expect(within(detail()).getByRole("heading").textContent).toBe(TEASER);
    expect(detail().querySelector("blockquote")).toBeNull();

    const list = await showList();
    const first = within(list).getAllByRole("button")[0];
    if (first === undefined) throw new Error("the list has no rows");
    await userEvent.click(first);
    const card = detail();
    expect(within(card).getByText("From the abstract, word for word")).toBeTruthy();
    const quote = card.querySelector("blockquote")?.textContent ?? "";
    const quotes = data.graph.nodes.flatMap((n) => (n.kind === "finding" ? [n.quote] : []));
    expect(quotes).toContain(quote);
    expect(within(card).getByRole("link", { name: "Open on PubMed (opens in a new tab)" }).getAttribute("href")).toMatch(
      /^https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/999\d+\/$/,
    );
  });

  it("lets the keyboard reach every row of the list", async () => {
    render(<QuestionPage data={data} outcome={null} />);
    const list = await showList();
    const rows = within(list).getAllByRole("button");
    expect(rows.length).toBeGreaterThan(0);

    const reached = new Set<Element>();
    (document.activeElement as HTMLElement | null)?.blur();
    for (let i = 0; i < 300; i++) {
      await userEvent.tab();
      if (document.activeElement === document.body || reached.has(document.activeElement as Element)) {
        break;
      }
      reached.add(document.activeElement as Element);
    }

    expect(rows.filter((row) => !reached.has(row))).toEqual([]);
  });
});

describe("the synthetic fixture", () => {
  const data = fixtures.synthetic;

  it("groups the list by direction and marks studies shown but not counted", async () => {
    render(<QuestionPage data={data} outcome="Synthetic symptoms" />);
    const list = await showList();

    const groups = within(list)
      .getAllByRole("rowheader")
      .map((th) => th.textContent);
    expect(groups).toEqual([
      "Benefit · 3 studies, plus 1 not counted",
      "No clear difference · 1 study, plus 1 not counted",
      "Harm · 2 studies",
    ]);
    expect(within(list).getByText("Not counted: Subgroup results only")).toBeTruthy();
    expect(within(list).getByText("Not counted: Design without a weight")).toBeTruthy();
    expect(within(list).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Study",
      "Measured as",
      "Design",
      "n",
      "Year",
    ]);
  });

  it("shows a selected row's finding in the card", async () => {
    render(<QuestionPage data={data} outcome="Synthetic symptoms" />);
    const list = await showList();

    const row = within(list).getByRole("button", { name: "Synthetic106 et al. 2016" });
    await userEvent.click(row);

    expect(row.getAttribute("aria-pressed")).toBe("true");
    const card = detail();
    expect(within(card).getByRole("heading").textContent).toBe(
      "Synthetic study 106 of supplement X and mood",
    );
    // A case series is observational, so its badge reads as a link, not harm.
    expect(within(card).getByText("Linked to worse synthetic mood")).toBeTruthy();
    expect(within(card).getByText("Case series")).toBeTruthy();
  });

  it("marks a custom dimension that was not evaluated", async () => {
    render(<QuestionPage data={data} outcome="Synthetic symptoms" />);
    const list = await showList();
    await userEvent.click(within(list).getByRole("button", { name: "Synthetic102 et al. 2012" }));
    const card = detail();

    const fields = [...card.querySelectorAll(".shell-fields__row")].map((row) => [
      row.querySelector("dt")?.textContent,
      row.querySelector("dd")?.textContent,
    ]);
    // The card uses a dimension's short label where it has one.
    expect(fields).toContainEqual(["Baseline status", "Not reportedCustomNot evaluated"]);
    expect(fields).toContainEqual(["Synthetic dose", "50 mgCustom"]);
  });

  it("switches the strip, the list and the selection with the outcome", async () => {
    const { rerender } = render(<QuestionPage data={data} outcome="Synthetic symptoms" />);
    const list = await showList();
    await userEvent.click(within(list).getByRole("button", { name: "Synthetic106 et al. 2016" }));

    rerender(<QuestionPage data={data} outcome="Synthetic cases" />);
    expect(screen.getByRole("region", { name: "Summary for synthetic cases" })).toBeTruthy();
    expect(screen.getByText(/Thin evidence\./)).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Design-weighted share by direction" })).toBeNull();
    expect(screen.getByRole("link", { name: /^Synthetic cases/ }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(within(detail()).getByRole("heading").textContent).toBe(TEASER);

    rerender(<QuestionPage data={data} outcome="Synthetic symptoms" />);
    expect(within(detail()).getByRole("heading").textContent).toBe(
      "Synthetic study 106 of supplement X and mood",
    );
  });

  it("opens another outcome on the same study from the card", async () => {
    const { rerender } = render(<QuestionPage data={data} outcome="Synthetic symptoms" />);
    const list = await showList();
    await userEvent.click(within(list).getByRole("button", { name: "Synthetic101 et al. 2011" }));

    const also = within(detail()).getByRole("link", { name: "Also reports: synthetic remission" });
    expect(also.getAttribute("href")).toBe("#/outcome/Synthetic%20remission");
    await userEvent.click(also);
    rerender(<QuestionPage data={data} outcome="Synthetic remission" />);

    const card = detail();
    expect(within(card).getByRole("heading").textContent).toBe(
      "Synthetic study 101 of supplement X and mood",
    );
    expect(within(card).getAllByText("Synthetic remission").length).toBeGreaterThan(0);
    window.location.hash = "";
  });

  it("says when no outcome has the route's name", () => {
    render(<QuestionPage data={data} outcome="Nothing synthetic" />);

    expect(screen.getByRole("status").textContent).toContain("No outcome named Nothing synthetic");
    expect(screen.getByRole("heading", { name: "How this page was made" })).toBeTruthy();
  });

  it("states the method's counts, depth and costs", () => {
    render(<QuestionPage data={data} outcome={null} />);
    const method = screen.getByRole("region", { name: "How this page was made" });

    expect(method.textContent).toContain(
      "Contradictions on this page were classified at single-small depth. The model judged the 10 pairs whose studies are alike in at least 3 of 5 ways, with no study in " +
        "more than 8. The page shows only the closely matched ones, alike in all 5, each reported " +
        "by both studies: design, outcome measure, baseline synthetic status, synthetic dose and " +
        "sample size within 3×.",
    );
    // Candidate counts count pairs of studies, which reads as a count of findings.
    expect(method.textContent).not.toMatch(/candidate pairs|\b25\b/);
    // The step itself stays as short as the others.
    expect(screen.getByText(/^Findings on the same outcome/).textContent).toBe(
      "Findings on the same outcome that point different ways are paired, and a model judges the 10 " +
        "most alike, naming the likely reason.",
    );
    expect(method.textContent).toContain(
      "Measured spend over every recorded run of this question, evals included: $0.18. By stage: " +
        "retrieve $0.00 (1 run) · extract $0.11 (2 runs) · normalize $0.02 (1 run) · classify $0.05 (3 runs).",
    );
    // A count of one takes the singular.
    expect(method.textContent).toContain("1 finding whose quote was not verbatim");
    expect(method.textContent).toContain("3 findings outside the outcome domain");
    expect(method.textContent).toContain("52 PubMed records found");
  });
});

describe("the measured accuracy", () => {
  it("shows each extraction field once, with the findings matched over the model's and the labels'", () => {
    render(<QuestionPage data={fixtures.synthetic} outcome={null} />);
    const method = screen.getByRole("region", { name: "How this page was made" });
    expect(method.textContent).toContain("extraction, effect: precision 0.70 (21 of 30), recall 0.66 (21 of 32)");
    expect(method.textContent).not.toContain("effect recall");
    expect(method.textContent).toContain(
      "Precision is over the 30 findings the model returned, recall over the 32 labeled.",
    );
    expect(method.textContent).not.toMatch(/not equal by construction/);
  });

  it("says why precision and recall are equal when the model returned as many findings as were labeled", () => {
    const equal = JSON.parse(JSON.stringify(fixtures.synthetic)) as Export;
    for (const e of equal.method.evals) {
      if (e.metric.endsWith(" recall")) e.n = 30;
    }
    render(<QuestionPage data={equal} outcome={null} />);
    const method = screen.getByRole("region", { name: "How this page was made" });
    expect(method.textContent).toContain(
      "The model returned as many findings as were labeled, so each field's two figures are equal; " +
        "they are not equal by construction.",
    );
  });
});

describe("the method's compare line", () => {
  it("says how many chosen pairs have not been classified yet", () => {
    const data = JSON.parse(JSON.stringify(fixtures.synthetic)) as Export;
    if (data.method.selection) data.method.selection.unclassified = 2;
    render(<QuestionPage data={data} outcome={null} />);
    const method = screen.getByRole("region", { name: "How this page was made" });
    expect(method.textContent).toContain(
      "The model judged 10 of the 12 pairs whose studies are alike in at least 3 of 5 ways, with no " +
        "study in more than 8. The other 2 have not been classified yet.",
    );
  });
});

describe("the minimal fixture", () => {
  it("shows a thin strip and empty method lines", () => {
    render(<QuestionPage data={fixtures.minimal} outcome={null} />);

    expect(screen.getByText(/Thin evidence\./)).toBeTruthy();
    expect(screen.getByText("No systematic reviews or meta-analyses were found for this question.")).toBeTruthy();
    expect(screen.getByText(/No contradictions were classified/)).toBeTruthy();
  });
});
