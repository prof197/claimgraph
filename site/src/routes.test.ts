import { href, parseRoute, type Route } from "./routes";

const routes: Route[] = [
  { page: "question", outcome: null },
  { page: "question", outcome: "Synthetic symptoms" },
  { page: "question", outcome: "Synthetic symptoms", at: "contradictions" },
  { page: "pair", pairId: "Synthetic symptoms|999000101|999000102" },
];

it.each(routes)("round-trips %o through its hash", (route) => {
  expect(parseRoute(href(route))).toEqual(route);
});

it("reads an unknown hash as the question page", () => {
  expect(parseRoute("#/nowhere")).toEqual({ page: "question", outcome: null });
});
