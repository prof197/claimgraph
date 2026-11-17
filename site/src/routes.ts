// Hash routes, so the static build needs no server rewrites on GitHub Pages.

export type Route =
  // `at` lands on a section of the page, its heading focused, rather than at the top.
  | { page: "question"; outcome: string | null; at?: "contradictions" }
  | { page: "pair"; pairId: string };

export function parseRoute(hash: string): Route {
  const [, page, value, at] = hash.replace(/^#/, "").split("/");
  if (page === "pair" && value) {
    return { page: "pair", pairId: decodeURIComponent(value) };
  }
  if (page === "outcome" && value) {
    const outcome = decodeURIComponent(value);
    return at === "contradictions" ? { page: "question", outcome, at } : { page: "question", outcome };
  }
  return { page: "question", outcome: null };
}

export function href(route: Route): string {
  if (route.page === "pair") {
    return `#/pair/${encodeURIComponent(route.pairId)}`;
  }
  if (route.outcome === null) {
    return "#/";
  }
  const base = `#/outcome/${encodeURIComponent(route.outcome)}`;
  return route.at === undefined ? base : `${base}/${route.at}`;
}
