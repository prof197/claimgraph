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
  const response = await fetch("./export.json");
  if (!response.ok) {
    throw new Error(`could not load export.json: ${response.status}`);
  }
  return (await response.json()) as Export;
}
