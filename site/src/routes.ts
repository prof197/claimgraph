// Hash routes, so the static build needs no server rewrites on GitHub Pages.

export type Route =
  | { page: "question"; outcome: string | null }
  | { page: "pair"; pairId: string };

export function parseRoute(hash: string): Route {
  const [, page, value] = hash.replace(/^#/, "").split("/");
  if (page === "pair" && value) {
    return { page: "pair", pairId: decodeURIComponent(value) };
  }
  if (page === "outcome" && value) {
    return { page: "question", outcome: decodeURIComponent(value) };
  }
  return { page: "question", outcome: null };
}

export function href(route: Route): string {
  if (route.page === "pair") {
    return `#/pair/${encodeURIComponent(route.pairId)}`;
  }
  return route.outcome === null ? "#/" : `#/outcome/${encodeURIComponent(route.outcome)}`;
}
