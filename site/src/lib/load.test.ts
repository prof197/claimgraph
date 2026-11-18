import { loadExport } from "./load";

describe("loading the export", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("says how to get the export when the dev server answers with the page instead", async () => {
    // A fresh clone has no site/public/export.json, and Vite serves index.html in its place.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("<!doctype html>", { headers: { "content-type": "text/html" } }))),
    );
    await expect(loadExport("")).rejects.toThrow(/claimgraph export fetch.*\?fixture=synthetic/);
  });
});
