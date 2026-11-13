# Eval samples

One directory per question:

- `gold.jsonl`: the hand-labeled eval sample, with abstract text. Every article is CC BY or
  CC0, and its licence is recorded on its line. It holds the records that were labeled, kept
  from the draw by `claimgraph gold trim`.
- `ATTRIBUTION.md`: the source and licence of every gold abstract. Generated.
- `dev.jsonl`: the tuning sample, as PMIDs and strata only. Its abstracts may carry any licence,
  so their text is not committed; after the retrieve stage, `claimgraph gold sample` rebuilds
  it in `runs/`.
- `screening.toml`: the on- or off-topic reading of every record the sampler reached.
- `sample.json`: the seed, quotas, and the composition of the corpus and the open pool.
- `labels/`, `outcome_pairs.toml`, `contradiction_pairs.toml`: the hand labels, written by
  the rules in [LABELING.md](LABELING.md).

## Size

The vitamin D gold set is 27 hand-labeled abstracts, from a draw of 50: 14 sampled as trials, 8
as observational studies, 2 as systematic reviews, 2 as other reviews and 1 off topic.
`sample.json` records both counts. At this size the extraction eval's intervals are wide, about
±15 points on a field scored right 80% of the time, so it can show a field that is broken but
not separate two close prompts.

## Who labeled

The maintainer labeled every record. For 1 record (22848407), he discussed how the labeling
rules applied with an AI assistant before labeling; the rules that came out of those discussions
were added to [LABELING.md](LABELING.md).

After labeling, labels were checked against the written rules; the maintainer reviewed and
accepted 4 corrections:

| PMID | Field | Was | Now | Rule |
|---|---|---|---|---|
| 32108263 | `sample_size` | 280 | 113 | Randomized, not screened |
| 27485050 | `sample_size` | 105 | 117 | A secondary analysis counts those entering it, not those analyzed |
| 27485050 | `design` | `cross_sectional` | `cohort` | Vitamin D was measured before depression was assessed |
| 27485050 | `outcome_reported` | MDD | MDD (MINI) | The instrument is named when the abstract names one |

## Licence

The code in this repository is MIT-licensed. The abstract texts in `gold.jsonl` are not: they
keep their original CC BY or CC0 licences and are attributed in each question's
`ATTRIBUTION.md`.
