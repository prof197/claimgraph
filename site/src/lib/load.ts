import type { Export } from "../types/export";

const fixtures = import.meta.glob<Export>("../fixtures/*.json", { import: "default" });

/** The question's export: `export.json` beside the page, or in development `?fixture=name`
 * for one of the committed fixture exports. */
export async function loadExport(search: string = window.location.search): Promise<Export> {
  const fixture = new URLSearchParams(search).get("fixture");
  if (import.meta.env.DEV && fixture !== null) {
    const load = fixtures[`../fixtures/${fixture}.json`];
    if (load === undefined) {
      throw new Error(`no fixture export named ${fixture}`);
    }
    return load();
  }
  const response = await fetch(`${import.meta.env.BASE_URL}export.json`);
  // The dev server answers a missing file with the page itself, so a fresh clone gets HTML.
  if (import.meta.env.DEV && !(response.headers.get("content-type") ?? "").includes("json")) {
    throw new Error(
      "site/public/export.json is not there yet: run `uv run claimgraph export fetch`, or open /?fixture=synthetic.",
    );
  }
  if (!response.ok) {
    throw new Error(`could not load export.json: ${response.status}`);
  }
  return (await response.json()) as Export;
}
