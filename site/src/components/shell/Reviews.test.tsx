import { render, screen, within } from "@testing-library/react";
import { fixtures } from "../../fixtures";
import { Reviews } from "./Method";

it("lists systematic reviews and meta-analyses, and collapses narrative reviews with their count", () => {
  render(<Reviews reviews={fixtures.synthetic.reviews} />);

  const narrative = screen.getByText(/1 narrative review, without systematic methods/);
  const details = narrative.closest("details");
  expect(details?.open).toBe(false);
  const listed = screen.getAllByRole("list")[0];
  expect(listed && within(listed).getByText(/Pooled 12 synthetic trials/)).toBeTruthy();
  expect(within(details as HTMLElement).getAllByText(/Synthetic narrative review/).length).toBeGreaterThan(0);
});

it("says the reviews are listed for context, and makes no claim about what they pool", () => {
  render(<Reviews reviews={fixtures.synthetic.reviews} />);
  expect(screen.getByText("Listed for context, not counted above.")).toBeTruthy();
  expect(screen.queryByText(/already on this page/)).toBeNull();
});
