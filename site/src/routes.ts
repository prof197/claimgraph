// Path routes under the site's base path: /outcome/<name> and /pair/<id>. GitHub Pages has no
// rewrites, so the build also writes index.html as 404.html, and any deep link loads the page.

export type Route =
  // `at` lands on a section of the page, its heading focused, rather than at the top.
  | { page: "question"; outcome: string | null; at?: "contradictions" }
  | { page: "pair"; pairId: string };

/** The fragment that lands on the contradictions panel. */
export const CONTRADICTIONS = "where-studies-disagree";

const BASE = import.meta.env.BASE_URL;

function fromSegments(page: string | undefined, value: string | undefined, at: boolean): Route {
  if (page === "pair" && value) {
    return { page: "pair", pairId: decodeURIComponent(value) };
  }
  if (page === "outcome" && value) {
    const outcome = decodeURIComponent(value);
    return at ? { page: "question", outcome, at: "contradictions" } : { page: "question", outcome };
  }
  return { page: "question", outcome: null };
}

export function parseRoute(pathname: string, hash = ""): Route {
  const path = pathname.startsWith(BASE) ? pathname.slice(BASE.length) : pathname.replace(/^\//, "");
  const [page, value] = path.split("/");
  return fromSegments(page, value, hash === `#${CONTRADICTIONS}`);
}

/** A route from the hash addresses earlier versions used, `#/outcome/<name>/contradictions`
 * and `#/pair/<id>`, so links already shared still land. Null for any other hash. */
export function legacyRoute(hash: string): Route | null {
  if (!hash.startsWith("#/")) {
    return null;
  }
  const [page, value, at] = hash.slice(2).split("/");
  return fromSegments(page, value, at === "contradictions");
}

/** In development, links keep the fixture the page was opened on. */
function search(): string {
  if (!import.meta.env.DEV) {
    return "";
  }
  const fixture = new URLSearchParams(window.location.search).get("fixture");
  return fixture === null ? "" : `?fixture=${encodeURIComponent(fixture)}`;
}

export function href(route: Route): string {
  if (route.page === "pair") {
    return `${BASE}pair/${encodeURIComponent(route.pairId)}${search()}`;
  }
  if (route.outcome === null) {
    return `${BASE}${search()}`;
  }
  const path = `${BASE}outcome/${encodeURIComponent(route.outcome)}${search()}`;
  return route.at === undefined ? path : `${path}#${CONTRADICTIONS}`;
}
