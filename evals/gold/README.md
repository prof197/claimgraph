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
- `labels/`, `outcome_kinds.toml`, `contradiction_pairs.toml`: the hand labels, written by
  the rules in [LABELING.md](LABELING.md).
- `outcome_pair_sample.json`: the outcome pairs the normalization eval reports as a subset,
  with the stratum each came from and the recorded proposal it was drawn from. It shows the
  model's grouping, so it is opened only after every outcome string has a kind.

## Size

The vitamin D gold set is 32 hand-labeled abstracts, from a draw of 50, taken in the labeling
page's order within each stratum: 15 sampled as trials, 9 as observational studies, 2 as
systematic reviews, 2 as other reviews and 4 off topic. `sample.json` records both counts. At
this size the extraction eval's intervals are wide, about ±14 points on a field scored right 80%
of the time, so it can show a field that is broken but not separate two close prompts.

## Who labeled

Every label was made by the maintainer following [LABELING.md](LABELING.md). For 2 records
(22848407, 33372187), he discussed how the rules applied with an AI assistant before labeling;
the rules that came out of those discussions were added to LABELING.md. After labeling, an AI
coding assistant checked the labels against the written rules, and the maintainer reviewed and
accepted the first 10 corrections listed below. The 11th was made after the extraction eval
was first scored, because the label contradicted a rule that predates the label check.

| PMID | Field | Was | Now | Rule |
|---|---|---|---|---|
| 32108263 | `sample_size` | 280 | 113 | Randomized, not screened |
| 27485050 | `sample_size` | 105 | 117 | A secondary analysis counts those entering it, not those analyzed |
| 27485050 | `design` | `cross_sectional` | `cohort` | Vitamin D was measured before depression was assessed |
| 27485050 | `outcome_reported` | MDD | MDD (MINI) | The instrument is named when the abstract names one |
| 31640594 | `quote`, `baseline_vitamin_d_status` | The neuropsychology sentence; `mixed` | The depressive-symptoms sentence; `at_or_above_20_ng_ml` | Quote the sentence that reports the finding; a whole-sample mean settles the band before `mixed` |
| 29027946 | findings | One, on mental health overall | None | A measure combining depression with other outcomes is left out |
| 30061860 | `design` | `narrative_review` | `systematic_review` | It states how it searched and how it chose studies |
| 32629761 | findings | — | Women, `decreased` | One finding per subgroup the abstract reports |
| 32629761 | findings | — | White participants, `decreased` | One finding per subgroup the abstract reports |
| 30157761 | `baseline_vitamin_d_status` | `mixed` | `not_reported` | `mixed` needs values on both sides of 20 ng/mL; both group means are above it |
| 38931184 | findings | One, a before-and-after change in the vitamin D arm | None | Between groups only: a change within one arm is not a finding. Made after scoring |

### Outcome kinds

Every outcome string was given a kind by the maintainer on the labeling page, following the
outcome section of [LABELING.md](LABELING.md). The first 3 corrections below came from
comparing the kinds with the maintainer's earlier pair verdicts, before the normalization eval
was first scored. The other 17 were made after scoring: building the reviewed mapping from the
kinds showed labels that contradicted the guide's words, and the maintainer accepted each
correction.

| String | Was | Now | Rule | Scoring |
|---|---|---|---|---|
| elevated depressive symptoms (CES-D) | `score` | `case_status` | "Elevated" is a case-status word | Before |
| elevated depressive symptoms (8-point CES-D) | `score` | `case_status` | "Elevated" is a case-status word | Before |
| reduction of depressive symptoms | `response` | `score` | "Reduction" is a score word without a percentage threshold | Before |
| depression symptoms | `case_status` | `score` | "Symptoms" is a score word | After |
| symptoms of depression | `case_status` | `score` | "Symptoms" is a score word | After |
| depression (symptom) | `case_status` | `score` | "Symptoms" is a score word | After |
| depression symptom presentation | `case_status` | `score` | "Symptoms" is a score word | After |
| depressed mood | `case_status` | `score` | "Mood" beside a depression word is a score word | After |
| DASS-depression | `case_status` | `score` | A depression subscale with no cut-off is a score | After |
| DISYPS-II parental rating of depressive symptoms | `case_status` | `score` | A rating scale with no cut-off is a score | After |
| WURS depression | `case_status` | `score` | The WURS has a depression factor (Öncü et al. 2005), so it is a depression subscale | After |
| incident depression (GDS) | `score` | `case_status` | "Incident" is a case-status word, and words beat symbols | After |
| risk of depression (Beck Depression Inventory-II) | `score` | `case_status` | "Risk" is a case-status word | After |
| depression risk (HAM-D) | `score` | `case_status` | "Risk" is a case-status word | After |
| moderate-to-severe depression or major depression (PHQ-9) | `score` | `case_status` | "Major depression" is a case-status word | After |
| elevated depressive symptoms (depression screening score) | `score` | `case_status` | "Elevated" and "screening" are case-status words | After |
| comorbid depression | `score` | `case_status` | "Comorbid" names the population, not a second condition, so it is a bare condition | After |
| depression (Mental Health Inventory-5) at 50 years | `case_status` | `out_of_domain` | The MHI-5 has no depression-only part | After |
| psychiatric symptomatology (HSCL-56) | `score` | `out_of_domain` | A mixed instrument that does not say depression | After |
| mental health scores (PHQ-9, PROMIS) | `score` | `out_of_domain` | Mental health is broader than depression | After |

## Licence

The code in this repository is MIT-licensed. The abstract texts in `gold.jsonl` are not: they
keep their original CC BY or CC0 licences and are attributed in each question's
`ATTRIBUTION.md`.
