# Labeling guide

Three label sets feed the evals. Each is TOML, written by hand, and checked by
`claimgraph gold check QUESTION`.

| Labels | File | Eval |
|---|---|---|
| Abstracts | `QUESTION/labels/PMID.toml`, one per gold abstract | Extraction |
| Outcome kinds | `QUESTION/outcome_kinds.toml` | Normalization |
| Contradiction pairs | `QUESTION/contradiction_pairs.toml` | Contradictions |

Every value is explicit. TOML has no null, so a value the abstract does not give is written
`"not_reported"`. Write quotes as literal strings, `'''like this'''`, so a sentence containing
double quotes or backslashes needs no escaping. Synthetic examples of all three files are in
`tests/fixtures/labels/`; every example below is synthetic too.

## Abstracts

```
uv run claimgraph gold review vitamin-d-depression     # label on a local page
uv run claimgraph gold template vitamin-d-depression   # or: one template per abstract, by hand
uv run claimgraph gold check vitamin-d-depression      # run as often as you like
uv run claimgraph gold trim vitamin-d-depression       # when done: keep only the labeled records
```

The review page serves on 127.0.0.1 only. Clicking a sentence makes it a finding's quote,
selected words set the outcome, keys set the closed fields (press `?` for the list), and every
edit is saved to the label file and checked. It never fills in a value for you.

Each template shows the abstract as comments above the fields. Its blank values fail the check,
so an unfilled template never passes as a label. Templates are never overwritten.

Label what the abstract says, not what the full paper or your own knowledge says. Labels are
the truth the extraction model is scored against, so they follow the same rules the model is
given.

### Study fields

**`design`** is settled by the design questions, asked in order and stopping at the first answer
that names a design. Answer from the abstract, not from PubMed's publication types. A protocol
answers for its planned design. The review page asks the questions and sets the design; it can
also jump to a design, which fills in the answers on that design's path for you to check.

| # | Key | Question | Answers |
|---|---|---|---|
| 1 | `subjects` | Is it a study on people? | `people` → 2; `animals` → `animal`; `cells_or_tissue` → `in_vitro` |
| 2 | `source` | Does it report its own data, or summarize other studies? | `own_data` → 5; `summary` → 3; `neither`, such as a bibliometric study → `other` |
| 3 | `systematic_methods` | Does it say how it searched and how it chose studies, or call itself systematic, scoping, umbrella or a meta-analysis? | `yes` → 4; `no` → `narrative_review` |
| 4 | `pools` | Does it pool results into one number? | `yes` → `meta_analysis`; `no` → `systematic_review` |
| 5 | `individual_patients` | Does it describe one or a few patients individually? | `yes` → `case_report`; `no` → 6 |
| 6 | `gave_exposure` | Did the researchers give vitamin D, with results comparing depression by who got it, how much, or before and after? | `yes` → 7; `no` → 9 |
| 7 | `concurrent_comparison` | Is there a concurrent comparison group: placebo, none or another dose? | `yes` → 8; `no` → `case_series` |
| 8 | `by_chance` | Was who got what assigned by chance, to individuals or to clusters? | `yes` → `rct`; `no` → `non_randomized_trial` |
| 9 | `proxy_or_event` | Does the vitamin D exposure come only from genetic proxies or an outside event, such as a fortification policy? | `yes` → `other`; `no` → 10 |
| 10 | `selected_by_outcome` | Were participants picked because they had depression or did not? | `yes` → `case_control`; `no` → 11 |
| 11 | `measured_before` | Was vitamin D measured before depression was assessed? | `yes` → `cohort`; `no` → `cross_sectional` |

The design is the label; the answers are its provenance. They are optional and never scored,
but a label that has them must follow them: the check reports a missing answer, an answer to a
question the path never reaches, and a design that does not follow from the answers.

```toml
design = "rct"

[design_answers]
subjects = "people"
source = "own_data"
individual_patients = "no"
gave_exposure = "yes"
concurrent_comparison = "yes"
by_chance = "yes"
```

Reading the questions:

- Question 3: saying how it searched means more than naming the databases; saying how it chose
  studies means eligibility criteria, screening, or a count of included studies. A review that
  says it followed PRISMA answers yes. A review titled narrative that says both is systematic. Guidelines, position statements and perspectives that
  review others' results are summaries, so this question decides them too. PubMed's publication
  types do not settle it: some systematic reviews are tagged only Review, and recent records
  often carry no type at all.
- Question 6: vitamin D the participants chose to take themselves was not given by the
  researchers. A follow-up analysis of trial participants that does not compare the randomized
  arms answers no.
- Question 7: a single-arm study, where everyone gets vitamin D and outcomes are compared before
  and after, answers no, as does a comparison with historical controls only, an earlier group
  that did not get vitamin D. Both are `case_series`, even when the abstract calls them a trial.
  A single-arm study's effect is the change from before to after. A comparison group is people
  who got no vitamin D or a different dose; a group that differs only by depression status, such
  as healthy controls beside supplemented patients, is not one.
- Question 8: allocation by alternation, date of birth or clinic is not by chance.
- Question 9: Mendelian randomization and natural experiments answer yes.
- Question 10: yes when the study picked people with depression and people without it to
  compare; a sample of people with depression alone answers no.
- Question 11: a study that measures vitamin D and depression at the same visit answers no, even
  inside a cohort study that reports only that visit.
- A review protocol's summary says what the review will assess.

**`sample_size`**: participants enrolled or randomized, not analyzed or completed.

- A secondary analysis: the number entering this analysis, not the number analyzed.
- Several samples: their total.
- Only one count of participants in the abstract: use it.
- A planned size from a sample-size calculation is not a count: write `"not_reported"` unless
  an actual count appears.
- Mendelian randomization: cases plus controls in the main outcome dataset.
- Reviews: `"not_reported"`. A review's pooled total is neither enrolled nor randomized.

**`review_summary`**: optional, for `systematic_review`, `meta_analysis` and `narrative_review`
only: one sentence stating the review's conclusion in your words. The eval does not score it, so
it may be left blank. A review has no findings: it reports others' results, not its own, and
reviews are not counted.

### Findings

Add one `[[finding]]` block per finding in the question's outcome domain. For vitamin D:
depressive symptoms, depression diagnosis or incidence, remission, response, and seasonal
affective disorder. One finding per outcome, arm or subgroup the abstract reports a result for.
Null results are findings.

- **Time points.** Results on one measure at several time points are one finding when they
  agree, and one finding per time point when they don't.
- **Between groups only.** In a study with a comparison group, only comparisons between groups
  are findings. A change over time within one group, such as "scores fell in the vitamin D
  group", is not, and neither is one pooled across all groups, such as "scores improved in both
  arms". A Conclusion sentence that rests on a within-group change, such as "supplementation
  reduced depression scores" after a before-and-after result in one arm, is not a
  between-groups finding either.
- **"The rest were not significant."** When the abstract names vitamin D among the factors it
  tested and a sentence says the others were not significant, label a `no_change` finding
  quoting that sentence.
- **Genetic proxies.** Results based on a genetic proxy for vitamin D are findings only in a
  study whose design is `other`, such as Mendelian randomization, and only in the direction from
  vitamin D to depression. In a study that also measures vitamin D, skip the proxy results, so
  they are not counted under that study's design.

Leave out results on other outcomes, such as anxiety, mood swings, quality of life, fatigue or
mania. Leave out measures that combine depression with something else, such as a total
anxiety-and-depression score, psychological distress or the EQ-5D anxiety/depression item,
unless the abstract reports the depression part on its own. Depression-specific subscales of
mixed instruments count, such as HADS-D, the DASS-21 depression scale and POMS
depression-dejection. Anhedonia scales, such as RSAS and RPAS, are not depression measures. An abstract with no finding in the
domain has no `[[finding]]` blocks; add a one-line `note` saying why. Some gold abstracts were
sampled because they were screened off topic, and the eval checks that the model returns no
findings for them.

**`quote`**: the one sentence that reports the result, copied exactly. Quote the Results
sentence that reports the finding; use a Conclusion sentence only when no Results sentence
reports it. In an abstract without headings, quote the sentence that reports the measured
comparison. If the result spans two sentences, take the one with the result. One sentence can back several findings, such as two
subgroups, or incidence and recurrence: give each finding its own block with the same quote. The check tolerates only differences in whitespace and
curly versus straight quote marks.

**`outcome_reported`**: the measure as the abstract names it, including the instrument when the
abstract names one, such as `"depression severity (BDI)"` or `"incident depression"`. An
instrument's name alone is complete, such as `"PHQ-2"` or `"CES-D score"`.

**`effect`**: what happened to the measure on the side with more vitamin D, compared with the
side with less: `increased`, `decreased` or `no_change`. More vitamin D means a higher serum
level, a higher intake, a higher dose, or supplementation compared with placebo or none. In a
trial, it is the arm that got more: vitamin D against placebo, or the higher dose against the
lower. In a single-arm study, it is after supplementation against before. Turn a result stated the other way round, as deficiency compared with sufficiency, so that
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
| In a single-arm study, scores fell from baseline after supplementation | `decreased`; the design is `case_series` |
| Higher 25(OH)D was associated with more synthetic depressive symptoms | `increased` |

**`is_primary`**: `true` only when the abstract calls this the primary outcome or endpoint.
Co-primary outcomes are all `true`. A subgroup result on the primary outcome is `false`.
Otherwise `false`, even when it is the only outcome reported.

**`is_subgroup`**: `true` when the result covers only part of the analyzed sample, split by sex,
age, baseline vitamin D status or similar. A study that enrolled only one group, such as only
women or only deficient participants, reports that group as its population, not as a subgroup,
so its results are `false`. A subgroup result is never primary.

Interaction or moderation results, such as an effect found only in carriers of one variant who
also had one exposure, are subgroup findings: one per subgroup comparison, including the
subgroup where it was not observed, which is `no_change`. When the abstract reports
significance only for the interaction, its words decide each subgroup's direction.

**`subgroup`**: optional, for a subgroup result only: which part of the sample it covers, as the
abstract names it, such as `"synthetic group A"`. Name it whenever two findings share a quote and
an outcome, as when one sentence reports two subgroups: the check treats findings with the same
quote, outcome and subgroup as one finding entered twice.

**`note`**: optional, for anything a reviewer of the label should know.

### Baseline vitamin D status

Each finding takes one value for `baseline_vitamin_d_status`. The definition, word for word
from the question config:

> Baseline serum 25-hydroxyvitamin D band of this finding's population, cut at 20 ng/mL (50
> nmol/L), the deficiency cut-off in the Endocrine Society's 2011 guideline; its 2024 guideline
> endorses no thresholds, so the cut-off only groups studies. below_20_ng_ml: strictly below 20
> ng/mL (50 nmol/L). at_or_above_20_ng_ml: 20 ng/mL (50 nmol/L) or higher; exactly 20 ng/mL (50
> nmol/L) is at_or_above_20_ng_ml. A subgroup finding takes its subgroup's band. Assign the band
> by precedence: (a) if the population was restricted by vitamin D status at eligibility, use
> that restriction, but only when its whole range is strictly below 20 ng/mL (50 nmol/L) or at
> or above it, in either unit; a restriction that includes values on both sides, such as ≤20
> ng/mL, ≤50 nmol/L, <30 ng/mL or <75 nmol/L, does not settle the band; a restriction that
> settles it applies to every finding in the study, subgroups included; (b) otherwise, use the
> baseline mean or median the abstract reports for that population; a mean or median for the
> whole sample does not carry over to a subgroup; (c) use mixed only when the abstract reports
> values on both sides of 20 ng/mL with no single summary for that population; (d) otherwise
> not_reported. Words such as low, deficient or hypovitaminosis D without a number do not settle
> the band.

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
| Eligible if 25(OH)D was below 20 ng/mL; a subgroup result for women | (a) the restriction applies to every finding | `below_20_ng_ml` |
| Whole-sample baseline mean 14 ng/mL; a subgroup result for men, with no value of its own | (b) the whole-sample mean does not carry over; (c) nothing | `not_reported` |
| "Patients with low vitamin D", or "hypovitaminosis D", with no number | words without a number settle nothing | `not_reported` |
| Serum 25(OH)D not mentioned | (d) | `not_reported` |

### Second pass

A few records are labeled twice, blind, to measure how consistent the labels are:

```
uv run claimgraph gold relabel vitamin-d-depression          # draw 5, write blank templates
uv run claimgraph gold review vitamin-d-depression --relabel # label them on the page
uv run claimgraph gold agreement vitamin-d-depression        # field-level agreement
```

`gold relabel` draws once, at random with a fixed seed, from the records with findings, and only
when every gold record has a passing label. A review or an off-topic record carries only its
design and sample size, so it would say little about consistency. Its templates go in `relabel/`, and a copy of the first pass for those records in
`relabel/first/`, so later corrections to the gold labels don't move the agreement. With `--relabel` the page shows only those
records, and nothing from the first pass: not its labels, outcomes, notes or flags. Label them
by the same rules, without opening the first-pass files.

## Outcome kinds

Each reported outcome string gets one kind: what it measures. Normalization groups strings by
what they measure, so two strings belong under one normalized outcome exactly when they share a
kind. The normalization eval derives every pair's verdict from the kinds: out of domain if
either string is, merge if both have the same kind, separate otherwise.

Label blind. Give every string a kind before opening the mapping in `mappings/` or
`outcome_pair_sample.json`: both show how the model grouped the strings, and a label made after
seeing the grouping leans toward it.

| `kind` | What it measures | Better when |
|---|---|---|
| `score` | How depressed, as an average number | Lower |
| `case_status` | Being or becoming depressed, yes or no | Lower |
| `remission` | Treated patients who got well | Higher |
| `response` | Treated patients who improved by a set amount | Higher |
| `out_of_domain` | Not a depression outcome | |

### Out of domain

A string is `out_of_domain` if it does any of these:

- Mixes depression with anything else: anxiety, distress, "and/or", comorbid, or bundled
  conditions, as in "depression or anxiety".
- Names a mixed instrument without saying depression: plain HADS, DASS, POMS, BSI, SCL-90,
  PHQ-4, K6 or K10, GHQ, the EQ-5D item, the SF-12 or SF-36 mental component, WHO-5.
- Isn't depression: anxiety scales, RSAS or RPAS, PANSS, animal tests such as immobility time,
  antidepressant use alone, vitamin D itself.

A string that names depression with a mixed instrument means its depression subscale, but only
if the instrument has one: HADS, DASS, POMS, BSI, SCL-90, or the CBCL or YSR DSM-oriented
depressive problems scale. So "HADS depression" is in the domain. An instrument with no
depression-only part, such as the SF-36 or SF-12, is out of domain even when the string says
depressive symptoms. The PANSS has no depression-only subscale, so a string citing it is out of
domain. A subscale or factor of a depression-only scale, such as the GDS
dysphoria factor, is a depression score.

Mixing ways of detecting depression is fine; mixing conditions is not. Antidepressant use alone
is out of domain, but as one criterion of a depression case definition it is case status:
"incident depression (CES-D ≥10 or antidepressant use)".

Pairs with an out-of-domain string are left out of the scores and counted, because each such
string is a finding extraction should have dropped.

### Score

An average number. The words "score", "severity", "symptoms", "mood", "change" and
"reduction"; or a condition plus a rating scale with no cut-off and no status word, as in
"depression (BDI)". "depression" alone is a score. The hard cases: "depressive symptoms",
"depression" and "PHQ-9 score" are all scores, and "incident depression" is case status.

### Case status

Yes or no: being or becoming depressed. A cut-off with ≥, as in "CES-D ≥16". The words
"positive", "elevated", "high", "probable", "clinically relevant", "diagnosed", "MDD", "major
depression", "incident", "new-onset", "prevalence", "history of", "past", "risk", "odds",
"case" and "recurrence". Diagnostic criteria or interviews: DSM, ICD, MINI, SCID, CIDI. Health
records.

### Remission and response

Remission: "remission", "remitted", or treated patients falling ≤ or < a low line, as in
"HAMD ≤7". A remission cut-off stays remission, never case status. Response: "response",
"responders", "≥50% reduction".

### Words beat symbols

A word naming remission, response or case status decides the kind, whatever symbol the string
carries: "remission (HAMD ≤7)" is remission and "≥50% reduction in HAMD" is response. A ≥
cut-off with no such word is case status, even beside a score word: "depressive symptoms
(CES-D ≥16)".

A string that reports both a score and a cut-off for the same depression measure takes the kind
of the measure named first: "PHQ-9 severity and PHQ-9 ≥10" is a score. It stays in the domain,
because the rule against bundling is about mixing conditions, not kinds.

The kind never depends on scale, version, rater, timing or population. Postpartum, seasonal,
adolescents and older adults name who was studied: a seasonal affective disorder score is a
score, and its diagnosis or incidence is case status. Who was studied is weighed later, as a
population reason for a contradiction.

### Polarity

Lower is better for score and case status; higher is better for remission and response. The
mapping review sets each outcome's direction from its kind.

Judge each string as written. If one is too vague to place, take the closest reading and mark
it unsure. The mapping review applies the same rules.

### The file

```
uv run claimgraph gold outcome-kinds template vitamin-d-depression  # every string, no kinds
uv run claimgraph gold outcome-kinds review vitamin-d-depression    # label on a local page
uv run claimgraph gold outcome-kinds check vitamin-d-depression     # validate the kinds
uv run claimgraph gold outcome-kinds compare vitamin-d-depression   # against entered pairs
```

The template lists every string of the recorded proposal once, sorted. The page from `review`,
on 127.0.0.1, shows each string alone in a fixed shuffled order with this section beside it,
and saves every click to `outcome_kinds.toml`; keys 1 to 5 set a kind and `u` marks a string
unsure. `compare` lists the pair verdicts entered by hand in `outcome_pairs.toml` that the kinds
do not give.

```toml
[[outcome]]
string = "PHQ-9 score"
kind = "score"

[[outcome]]
string = "seasonal mood pattern"
kind = "score"
unsure = true
```

## Contradiction pairs

Each pair is two studies whose headline findings on one normalized outcome point different
ways. A study's headline finding on an outcome is its primary finding on that outcome, else its
first-reported finding there that is not a subgroup result, the same finding the summary strip
counts. A study with only subgroup findings on an outcome has no headline finding there, so it
is not paired on it. `study_a` and `study_b` are
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
