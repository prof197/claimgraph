import { useEffect, useState } from "react";
import { PairPage } from "./components/contradictions/PairPage";
import { QuestionPage } from "./components/shell/QuestionPage";
import { loadExport } from "./lib/load";
import { useRoute } from "./lib/useRoute";
import type { Export } from "./types/export";

type Loaded = { state: "loading" } | { state: "ready"; data: Export } | { state: "failed"; error: string };

export function App({ load = loadExport }: { load?: () => Promise<Export> }) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const route = useRoute();

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

  if (loaded.state === "loading") {
    return <p role="status">Loading…</p>;
  }
  if (loaded.state === "failed") {
    return <p role="alert">The page's data could not be loaded. {loaded.error}</p>;
  }
  return route.page === "pair" ? (
    <PairPage data={loaded.data} pairId={route.pairId} />
  ) : (
    <QuestionPage data={loaded.data} outcome={route.outcome} at={route.at} />
  );
}
