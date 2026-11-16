// How the page names a study: one short citation everywhere a study is listed, drawn or paired,
// and one full citation under a title.
import type { Study } from "../types/export";

/** "Park et al. 2019", or the PMID and year when PubMed lists no author. */
export function shortCitation(study: Study): string {
  return study.first_author === null
    ? `PMID ${study.pmids[0]} (${study.year})`
    : `${study.first_author} et al. ${study.year}`;
}

/** "Park et al. · Chronobiol Int · 2019", for under a study's title. */
export function fullCitation(study: Pick<Study, "first_author" | "journal" | "year">): string {
  const author = study.first_author === null ? [] : [`${study.first_author} et al.`];
  return [...author, study.journal, String(study.year)].join(" · ");
}
