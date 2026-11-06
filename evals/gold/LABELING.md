# Labeling guide

Three label sets feed the evals. Each is TOML, written by hand, and checked by
`claimgraph gold check QUESTION`.

| Labels | File | Eval |
|---|---|---|
| Abstracts | `QUESTION/labels/PMID.toml`, one per gold abstract | Extraction |
| Outcome pairs | `QUESTION/outcome_pairs.toml` | Normalization |
| Contradiction pairs | `QUESTION/contradiction_pairs.toml` | Contradictions |

Every value is explicit. TOML has no null, so a value the abstract does not give is written
`"not_reported"`. Write quotes as literal strings, `'''like this'''`, so a sentence containing
double quotes or backslashes needs no escaping. Synthetic examples of all three files are in
`tests/fixtures/labels/`; every example below is synthetic too.

## Abstracts

```
uv run claimgraph gold template vitamin-d-depression   # one template per gold abstract
uv run claimgraph gold check vitamin-d-depression      # run as often as you like
```

Each template shows the abstract as comments above the fields. Its blank values fail the check,
so an unfilled template never passes as a label. Templates are never overwritten.

Label what the abstract says, not what the full paper or your own knowledge says. Labels are
the truth the extraction model is scored against, so they follow the same rules the model is
given.

### Study fields

**`design`**, from the abstract, not from PubMed's publication types:

| Value | Use for |
|---|---|
| `rct` | Participants randomized to vitamin D or a comparator, including dose comparisons |
| `non_randomized_trial` | An intervention without randomization, including single-arm pre-post trials |
| `cohort` | People followed over time, prospectively or retrospectively, including follow-up analyses of trial participants that do not compare the randomized arms |
| `case_control` | People with and without depression compared on vitamin D |
| `cross_sectional` | Vitamin D and depression measured at one time |
| `case_series` | A descriptive series of patients |
| `systematic_review` | A review with systematic methods, without pooling, including scoping and umbrella reviews |
| `meta_analysis` | A systematic review that pools results |
| `narrative_review` | A review without systematic methods, including perspectives, commentaries, guidelines and position statements that review others' results |
| `case_report` | One or a few patients described |
| `animal`, `in_vitro` | Non-human or laboratory studies |
| `other` | A primary study that fits none of the above, such as Mendelian randomization or a bibliometric analysis |

A review has systematic methods when its abstract calls it systematic, scoping, umbrella or a
meta-analysis, or describes both how it searched and how it chose studies: eligibility criteria,
screening, or a count of included studies. A review that only names the databases it searched is
narrative. A review titled narrative that describes both a search and a selection is systematic.
PubMed's publication types do not settle this: some systematic reviews are tagged only Review,
and recent records often carry no type at all.

**`sample_size`**: participants enrolled or randomized, not analyzed or completed. When the
abstract gives only one count of participants, use it. For reviews, write `"not_reported"`: a
review's pooled total is neither enrolled nor randomized.

**`review_summary`**: for `systematic_review`, `meta_analysis` and `narrative_review` only, one
sentence stating the review's conclusion in your words. A review has no findings: it reports
others' results, not its own, and reviews are not counted.

### Findings

Add one `[[finding]]` block per finding in the question's outcome domain. For vitamin D:
depressive symptoms, depression diagnosis or incidence, remission, response, and seasonal
affective disorder. One finding per outcome, arm or subgroup the abstract reports a result for.
Null results are findings.

Leave out results on other outcomes, such as anxiety, mood swings, quality of life, fatigue or
mania. Leave out measures that combine depression with something else, such as a total
anxiety-and-depression score, psychological distress or the EQ-5D anxiety/depression item,
unless the abstract reports the depression part on its own. Depression-specific subscales of
mixed instruments count, such as HADS-D, the DASS-21 depression scale and POMS
depression-dejection. An abstract with no finding in the
domain has no `[[finding]]` blocks; add a one-line `note` saying why. Eight gold abstracts were
sampled because they were screened off topic, and the eval checks that the model returns no
findings for them.

**`quote`**: the one sentence that reports the result, copied exactly. If the result spans two
sentences, take the one with the result. The check tolerates only differences in whitespace and
curly versus straight quote marks.

**`outcome_reported`**: the measure as the abstract names it, such as `"CES-D score"`,
`"depressive symptoms (BDI-II)"` or `"incident depression"`.

**`effect`**: what happened to the measure on the side with more vitamin D, compared with the
side with less: `increased`, `decreased` or `no_change`. More vitamin D means a higher serum
level, a higher intake, a higher dose, or supplementation compared with placebo or none. In a
trial, it is the arm that got more: vitamin D against placebo, or the higher dose against the
lower. Turn a result stated the other way round, as deficiency compared with sufficiency, so that
more vitamin D is the exposure side. When an abstract reports the association with depression as
the exposure, as in "depressed patients had lower vitamin D", read it with vitamin D as the
exposure: that is `decreased`, and `outcome_reported` is the depression measure, not the vitamin
D level. The quote stays verbatim.

| The abstract says | `effect` |
|---|---|
| Synthetic depression scores fell more with vitamin D than with placebo | `decreased` |
| Vitamin D deficiency was associated with higher odds of synthetic depression | `decreased` |
| Serum 25(OH)D correlated inversely with a synthetic depression score | `decreased` |
| Scores improved in both arms, with no difference between them | `no_change` |
| A non-significant trend toward lower scores with vitamin D | `no_change` |
| Synthetic scores fell more with the higher dose than with the lower dose | `decreased` |
| Patients with synthetic depression had lower 25(OH)D than controls | `decreased`; the outcome is synthetic depression |
| In an uncontrolled study, scores fell from baseline after supplementation | `decreased` |
| Higher 25(OH)D was associated with more synthetic depressive symptoms | `increased` |

**`is_primary`**: `true` only when the abstract calls this the primary outcome or endpoint.
Co-primary outcomes are all `true`. A subgroup result on the primary outcome is `false`.
Otherwise `false`, even when it is the only outcome reported.

**`note`**: optional, for anything a reviewer of the label should know.

### Baseline vitamin D status

Each finding takes one value for `baseline_vitamin_d_status`. The definition, word for word
from the question config:

> Baseline serum 25-hydroxyvitamin D band of this finding's population, cut at 20 ng/mL
> (50 nmol/L), the deficiency cut-off in the Endocrine Society's 2011 guideline; its 2024
> guideline endorses no thresholds, so the cut-off only groups studies. below_20_ng_ml: strictly
> below 20 ng/mL (50 nmol/L). at_or_above_20_ng_ml: 20 ng/mL (50 nmol/L) or higher; exactly 20
> ng/mL (50 nmol/L) is at_or_above_20_ng_ml. A subgroup finding takes its subgroup's band.
> Assign the band by precedence: (a) if the population was restricted by vitamin D status at
> eligibility, use that restriction, but only when its whole range is strictly below 20 ng/mL
> (50 nmol/L) or at or above it, in either unit; a restriction that includes values on both
> sides, such as ≤20 ng/mL, ≤50 nmol/L, <30 ng/mL or <75 nmol/L, does not settle the band; (b)
> otherwise, use the baseline mean or median the abstract reports for that population; (c) use
> mixed only when the abstract reports values on both sides of 20 ng/mL with no single summary
> for that population; (d) otherwise not_reported.

Worked examples:

| The abstract says | Rule | Value |
|---|---|---|
| Eligible if 25(OH)D was below 20 ng/mL | (a) the whole range is below | `below_20_ng_ml` |
| Eligible if 25(OH)D was 50 nmol/L or higher | (a) the whole range is at or above | `at_or_above_20_ng_ml` |
| Eligible if 25(OH)D was below 30 ng/mL; baseline mean 14 ng/mL | (a) straddles, so (b) | `below_20_ng_ml` |
| Eligible if 25(OH)D was 20 ng/mL or lower; baseline mean 17 ng/mL | (a) includes 20, which is at or above, so it straddles; (b) | `below_20_ng_ml` |
| Baseline mean 25(OH)D exactly 20 ng/mL | (b) exactly 20 counts as at or above | `at_or_above_20_ng_ml` |
| Baseline median 62 nmol/L | (b) 62 nmol/L is at or above 50 | `at_or_above_20_ng_ml` |
| 40% deficient (below 20 ng/mL) and 60% sufficient, no mean | (c) both sides, no summary | `mixed` |
| Eligible if below 30 ng/mL; no baseline value given | (a) straddles, (b) nothing, (c) nothing | `not_reported` |
| Subgroup result for participants below 20 ng/mL | the subgroup's own band | `below_20_ng_ml` |
| Serum 25(OH)D not mentioned | (d) | `not_reported` |

## Outcome pairs

Each pair asks whether two reported outcome strings belong under one normalized outcome, so
that a reader sees them in one tab. The strings come from the extracted findings; pairs are
sampled when the mapping is reviewed.

```toml
[[pair]]
a = "PHQ-9 score"
b = "depressive symptoms"
merge = true
```

Merge when both strings measure the same construct, whatever the instrument. Do not merge
different constructs. The hard cases, which the eval must include: "depressive symptoms",
"depression" and "PHQ-9 score" merge; "depression incidence" and "depression severity" do not.
One trial can report both an incidence and a symptom score, so one paper can exercise the rule.
Label each pair once, in either order. Polarity, whether higher is better, is set in the
mapping review, not here.

## Contradiction pairs

Each pair is two studies whose headline findings on one normalized outcome point different
ways. A study's headline finding on an outcome is its primary finding on that outcome, else its
first-reported one, the same finding the summary strip counts. `study_a` and `study_b` are
written in the order the candidate pair gives them.

```toml
[[pair]]
outcome = "depressive symptoms"
study_a = "999000001"
study_b = "999000002"
verdict = "genuine"
reason = "population"
also_differs = ["intervention"]
detail = "baseline 25(OH)D: 12 vs 31 ng/mL"
```

**`verdict`** is about the results, not whether a reason was found:

- `genuine`: the two results really point different ways. An observational association against
  a null trial is genuine, with reason `design_or_bias`.
- `apparent`: on a close read, they don't conflict. Either the null study could not rule out the
  effect, because the abstract calls it a pilot, a feasibility study or underpowered, or its
  confidence interval includes the other study's effect; or the two findings don't measure the
  same thing despite the shared outcome. There is no sample-size cutoff for underpowered: use the
  abstract's own signals.

**`reason`**: for a genuine pair, the difference most likely to explain it, or `unclear` when
none stands out. An apparent pair still gets a reason, never `unclear`: the one that dissolves
the conflict. A null that could not rule out the effect is `design_or_bias`; findings that
measure different things are `outcome_measure`.

| Reason | Covers |
|---|---|
| `population` | Who was enrolled, including baseline vitamin D status |
| `intervention` | Dose, duration, route, form, co-treatment |
| `comparator` | Placebo, active treatment, usual care |
| `outcome_measure` | Definition, instrument or timing, within one normalized outcome |
| `design_or_bias` | Study type; bias the abstract reports, such as open-label or high dropout; a null that could not rule out the effect |
| `unclear` | For a genuine pair only: no difference stands out |

**`also_differs`**: other differences present, never the primary reason again and never
`unclear`. **`detail`**: a short specific, such as "duration: 8 weeks vs a median 5.3 years".
An apparent pair needs one, saying what dissolves the conflict, such as "underpowered: n = 40".
**`note`**: optional.
