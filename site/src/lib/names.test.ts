import { fixtures } from "../fixtures";
import { fullCitation, shortCitation } from "./names";

const studies = fixtures.synthetic.studies;
const named = studies.find((s) => s.first_author !== null);
const unnamed = studies.find((s) => s.first_author === null);

it("names a study by its first author and year", () => {
  expect(named && shortCitation(named)).toBe(`${named?.first_author} et al. ${named?.year}`);
});

it("names a study with no author by its PMID and year", () => {
  expect(unnamed && shortCitation(unnamed)).toBe(`PMID ${unnamed?.pmids[0]} (${unnamed?.year})`);
});

it("cites author, journal and year in full", () => {
  expect(named && fullCitation(named)).toBe(
    `${named?.first_author} et al. · ${named?.journal} · ${named?.year}`,
  );
});
