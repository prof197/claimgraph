# Synthetic creatine fixtures

Every record here is invented for the creatine-cognition CLI demo test; none is a real study.
PMIDs are 999-prefixed and every title, abstract and quote is marked "Synthetic".

- `efetch.xml`: six PubMed records as EFetch returns them. Three trials and a cross-sectional
  survey report findings, a systematic review reports none, and one record has no abstract, so
  retrieval keeps five.
- `extraction/PMID.json`: the extraction model's output text for each kept record, as a
  recorded response would carry it. One finding, grip strength, is outside the outcome domain.
- `normalization.json`: the outcome proposal's output text for the run's seven outcome strings,
  numbered in sorted order as the request lists them.

The test records each output under the key of the current request, so a change to a prompt
leaves these files valid; a change to an output schema means updating them.
