import { act, render, screen } from "@testing-library/react";
import { App } from "./App";
import { fixtures } from "./fixtures";
import { href } from "./routes";

afterEach(() => {
  window.location.hash = "";
});

describe.each(Object.entries(fixtures))("the %s fixture", (_, data) => {
  it("renders its question page", async () => {
    render(<App load={() => Promise.resolve(data)} />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveProperty(
      "textContent",
      data.question.title,
    );
    expect(screen.getByRole("navigation", { name: "Outcomes" })).toBeTruthy();
  });
});

it("opens a pair page from its route", async () => {
  const data = fixtures.synthetic;
  const pair = data.graph.edges.find((e) => e.kind === "contradiction");
  if (pair === undefined || pair.kind !== "contradiction") {
    throw new Error("the synthetic fixture has a contradiction");
  }
  render(<App load={() => Promise.resolve(data)} />);
  await screen.findByRole("heading", { level: 1 });

  act(() => {
    window.location.hash = href({ page: "pair", pairId: pair.pair_id });
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  expect((await screen.findByRole("heading", { level: 1 })).textContent).toContain(
    "Contradiction",
  );
});

it("says when the data cannot be loaded", async () => {
  render(<App load={() => Promise.reject(new Error("synthetic failure"))} />);

  expect((await screen.findByRole("alert")).textContent).toContain("synthetic failure");
});
