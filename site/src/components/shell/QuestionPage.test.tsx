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
      "No effect · 1 study, plus 1 not counted",
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
    expect(within(card).getByText("Harm")).toBeTruthy();
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

    expect(method.textContent).toContain("10 of 25 candidate pairs classified");
    expect(method.textContent).toContain(
      "They are the pairs whose two studies are alike in at least 3 of 5 ways, each reported by " +
        "both: design, outcome measure, baseline synthetic status, synthetic dose and sample size " +
        "within 3×, with no study in more than 8.",
    );
    expect(method.textContent).toContain("The other 15 were not classified");
    expect(method.textContent).toContain("classified at single-small depth");
    expect(method.textContent).toContain(
      "This run cost $0.18, measured: extract $0.11 · normalize $0.02 · classify $0.05.",
    );
    expect(method.textContent).toContain("52 PubMed records found");
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
