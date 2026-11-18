import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { PairPage } from "./components/contradictions/PairPage";
import { QuestionPage } from "./components/shell/QuestionPage";
import { loadExport } from "./lib/load";
import { useLinkFollowing, useNavigation } from "./lib/useRoute";
import type { Route } from "./routes";
import type { Export } from "./types/export";

type Loaded = { state: "loading" } | { state: "ready"; data: Export } | { state: "failed"; error: string };

/** A different page, not another outcome's tab on the same one. */
function newPage(from: Route, to: Route): boolean {
  if (from.page !== to.page) return true;
  return from.page === "pair" && to.page === "pair" && from.pairId !== to.pairId;
}

/** Scrolls back to where the reader was; the graph sizes itself a frame or two after the page
 * renders, so the page may not be tall enough yet on the first try. */
function restoreScroll(y: number) {
  let tries = 0;
  const attempt = () => {
    window.scrollTo(0, y);
    if (Math.abs(window.scrollY - y) > 1 && ++tries < 20) window.requestAnimationFrame(attempt);
  };
  attempt();
}

export function App({ load = loadExport }: { load?: () => Promise<Export> }) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const { id, route, arrival, scroll } = useNavigation();
  const previous = useRef<Route | null>(null);
  useLinkFollowing();

  useEffect(() => {
    let live = true;
    load().then(
      (data) => live && setLoaded({ state: "ready", data }),
      (error: unknown) => live && setLoaded({ state: "failed", error: String(error) }),
    );
    return () => {
      live = false;
    };
  }, [load]);

  // A link to another page starts at its top, as a new page would; back and forward return to
  // where the reader was. A link that lands on a section scrolls there itself.
  const ready = loaded.state === "ready";
  useLayoutEffect(() => {
    if (!ready) return;
    const from = previous.current;
    previous.current = route;
    if (arrival === "history") {
      restoreScroll(scroll ?? 0);
    } else if (arrival === "link" && from !== null && newPage(from, route) && !("at" in route && route.at)) {
      window.scrollTo(0, 0);
    }
    // Once per move through history: the route is a new object on every render.
  }, [ready, id]);

  if (loaded.state === "loading") {
    return <p role="status">Loading…</p>;
  }
  if (loaded.state === "failed") {
    return <p role="alert">The page's data could not be loaded. {loaded.error}</p>;
  }
  return route.page === "pair" ? (
    <PairPage data={loaded.data} pairId={route.pairId} />
  ) : (
    <QuestionPage
      data={loaded.data}
      outcome={route.outcome}
      at={arrival === "history" ? undefined : route.at}
    />
  );
}
