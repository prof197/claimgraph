# Eval samples

One directory per question:

- `gold.jsonl`: the hand-labeled eval sample, with abstract text. Every article is CC BY or
  CC0, and its licence is recorded on its line.
- `ATTRIBUTION.md`: the source and licence of every gold abstract. Generated.
- `dev.jsonl`: the tuning sample, as PMIDs and strata only. Its abstracts may carry any licence,
  so their text is not committed; after the retrieve stage, `claimgraph gold sample` rebuilds
  it in `runs/`.
- `screening.toml`: the on- or off-topic reading of every record the sampler reached.
- `sample.json`: the seed, quotas, and the composition of the corpus and the open pool.
- `labels/`, `outcome_pairs.toml`, `contradiction_pairs.toml`: the hand labels, written by
  the rules in [LABELING.md](LABELING.md).

## Licence

The code in this repository is MIT-licensed. The abstract texts in `gold.jsonl` are not: they
keep their original CC BY or CC0 licences and are attributed in each question's
`ATTRIBUTION.md`.
