import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtures } from "../../fixtures";
import { SummaryStrip } from "./SummaryStrip";

const data = fixtures.synthetic;
const symptoms = data.outcomes[0]!;

it("counts the studies and lists their designs", () => {
  render(<SummaryStrip data={data} outcome={symptoms} />);

  expect(screen.getByText("primary studies measured synthetic symptoms")).toBeTruthy();
  expect(screen.getByText("3 RCTs · 1 cohort · 1 cross-sectional · 1 case series")).toBeTruthy();
  expect(screen.getByText(/Shown but not counted: 1 study with subgroup results only/)).toBeTruthy();
});

it("says each segment's share on keyboard focus and on hover", async () => {
  render(<SummaryStrip data={data} outcome={symptoms} />);
  const bar = screen.getByRole("group", { name: "Design-weighted share by direction" });
  const segments = within(bar).getAllByRole("img");

  expect(segments.map((s) => s.getAttribute("aria-label"))).toEqual([
    "Benefit: 64% of the design-weighted evidence",
    "No clear difference: 24% of the design-weighted evidence",
    "Harm: 12% of the design-weighted evidence",
  ]);

  await userEvent.tab();
  expect(document.activeElement).toBe(segments[0]);
  expect(screen.getByText("Benefit: 64% of the design-weighted evidence", { selector: "p" })).toBeTruthy();

  fireEvent.mouseEnter(segments[2]!);
  expect(screen.getByText("Harm: 12% of the design-weighted evidence", { selector: "p" })).toBeTruthy();
});

it("says when a weighting changes the leading direction", () => {
  const { container } = render(<SummaryStrip data={data} outcome={symptoms} />);
  const lines = container.querySelector("dl");

  const rows = [...(lines?.querySelectorAll("div") ?? [])].map((d) => d.textContent);
  expect(rows).toEqual([
    "Design-weightedBenefit leads",
    "Unweighted countBenefit leads",
    "Size-awareNo clear difference leadsChanges",
  ]);
  expect(screen.getByText("Weighting by sample size changes which direction leads.")).toBeTruthy();
});

it("leaves out empty segments", () => {
  const remission = data.outcomes[2]!;
  render(<SummaryStrip data={data} outcome={remission} />);
  const bar = screen.getByRole("group", { name: "Design-weighted share by direction" });

  expect(within(bar).getAllByRole("img").map((s) => s.getAttribute("aria-label"))).toEqual([
    "Benefit: 40% of the design-weighted evidence",
    "No clear difference: 60% of the design-weighted evidence",
  ]);
  expect(screen.getByText("Every weighting gives the same leading direction.")).toBeTruthy();
});

it("shows counts only for a thin outcome and says the evidence is thin", () => {
  const cases = data.outcomes[1]!;
  render(<SummaryStrip data={data} outcome={cases} />);

  expect(screen.queryByRole("group", { name: "Design-weighted share by direction" })).toBeNull();
  expect(screen.queryByText("Does the weighting change the picture?")).toBeNull();
  expect(screen.getByText(/Thin evidence\./)).toBeTruthy();
  expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "Benefit · 1 study",
    "No clear difference · 1 study",
    "Harm · 1 study",
  ]);
});
