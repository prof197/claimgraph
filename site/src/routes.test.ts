import { href, legacyRoute, parseRoute, type Route } from "./routes";

const routes: Route[] = [
  { page: "question", outcome: null },
  { page: "question", outcome: "Synthetic symptoms" },
  { page: "question", outcome: "Synthetic symptoms", at: "contradictions" },
  { page: "pair", pairId: "Synthetic symptoms|999000101|999000102" },
];

it.each(routes)("round-trips %o through its address", (route) => {
  const url = new URL(href(route), "https://example.org");
  expect(parseRoute(url.pathname, url.hash)).toEqual(route);
});

it("addresses routes as paths, with a fragment for the contradictions", () => {
  expect(routes.map(href)).toEqual([
    "/",
    "/outcome/Synthetic%20symptoms",
    "/outcome/Synthetic%20symptoms#where-studies-disagree",
    "/pair/Synthetic%20symptoms%7C999000101%7C999000102",
  ]);
});

it("reads an unknown path as the question page", () => {
  expect(parseRoute("/nowhere")).toEqual({ page: "question", outcome: null });
});

it("reads the hash addresses earlier versions used, and no other hash", () => {
  expect(legacyRoute("#/pair/Synthetic%20symptoms%7C999000101%7C999000102")).toEqual(routes[3]);
  expect(legacyRoute("#/outcome/Synthetic%20symptoms/contradictions")).toEqual(routes[2]);
  expect(legacyRoute("#/")).toEqual(routes[0]);
  expect(legacyRoute("#method-h")).toBeNull();
  expect(legacyRoute("")).toBeNull();
});
