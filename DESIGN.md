# Claimgraph design

Claimgraph turns about 500 PubMed abstracts on one health question into a contradiction map a
general reader can trust, for under $15 a run. This file records the problem, the decisions and
the alternatives rejected, the limits we expect, and the numbers we measure. It grows with the
code.

## Problem and constraints

A reader who asks "does vitamin D affect depression?" meets hundreds of studies that seem to
disagree. Most disagreements have a reason: the studies enrolled different people, gave the
treatment differently, compared it with different things, measured the outcome differently or
used weaker designs. Claimgraph extracts each finding from its abstract, puts findings that
measure the same outcome side by side, and names the likely reason when they disagree. It
navigates existing evidence; it never generates hypotheses or gives medical advice.

- **Abstracts only.** Full text would come only from the licensed PMC Open Access subset, and
  only if abstracts prove too thin.
- **One question end to end in v1.** Vitamin D and depression is the hosted page; creatine and
  cognition is the CLI demo.
- **PubMed E-utilities.** 3 requests per second without an API key, 10 with one; large jobs on
  weekends or between 9 pm and 5 am US Eastern on weekdays; `tool` and `email` parameters
  identify the client ([NLM][nlm-limits], [NCBI][ncbi-guidelines]).
- **Licensing.** Abstracts may be under publisher copyright ([NLM][nlm-copyright]). The page
  quotes single sentences with links, and committed gold data comes only from openly licensed
  articles.
- **Language.** 97% of MEDLINE citations from 2020–2023 are English-language articles
  ([NLM][nlm-lang]). Abstracts in other languages sit in PubMed's `OtherAbstract` element
  ([DTD][dtd-other]). v1 extracts from the English abstract.
- **Budget.** $30–100 all in; one full run on about 500 abstracts under $15.
- **Runs without an API key.** A replay mode serves recorded responses, and CI never calls a
  model.
- **Static hosting.** No live queries and no ongoing cost.
- **Never all pairs.** Comparisons happen only within a normalized outcome, between studies whose
  directions differ.

## Pipeline

Six steps, each writing a file the next one reads, so any step reruns alone and every
intermediate result can be inspected. Only extraction, normalization and classification call a
model; everything else is deterministic.

| Step | Reads | Writes | Who decides | Cached by |
| --- | --- | --- | --- | --- |
| 1. Retrieve | `questions/<id>.toml` | `runs/<id>/raw/<pmid>.xml`, `abstracts.jsonl` | Deterministic | PMID |
| 2. Extract | abstracts | `findings.jsonl` | Model, validated by Pydantic | Request hash |
| 3. Normalize | unique outcome strings | `mappings/<id>.toml` (committed) | Model proposes, a person reviews | The mapping file |
| 4. Compare | findings and mapping | `pairs.jsonl`, `classifications.jsonl` | Pairs deterministic; model classifies | Request hash |
| 5. Build graph | all of the above | `site/data/<id>.json` | Deterministic | — |
| 6. Render | graph JSON | static site | Deterministic | — |

The pipeline is plain functions and direct SDK calls. Every flow is fixed in advance, so an agent
framework would add indirection without adding a decision it could make.

## Data model

Every model output is validated by a Pydantic v2 model, and each stage file is JSON Lines of
those models. One abstract yields one Study and one or more Findings, because a trial can report
several outcomes, arms or subgroups.

| Model | Field | Type | Notes |
| --- | --- | --- | --- |
| Study | `pmid` | str | One per PubMed record |
| Study | `title`, `first_author`, `journal` | str, str or None, str | From PubMed metadata; the display label defaults to first author and year |
| Study | `trial_ids` | list[str] | Registrations from any trial registry, as `REGISTRY:ACCESSION`; papers linked through any shared ID count once |
| Study | `year` | int | |
| Study | `language` | str | Article language, from PubMed |
| Study | `design` | Design | From PubMed publication types when they name it; the model otherwise |
| Study | `sample_size` | int or None | Enrolled or randomized, not analyzed |
| Finding | `finding_id` | str | `<pmid>-<n>`, assigned by code |
| Finding | `claim` | str | One plain sentence written by the model |
| Finding | `quote` | str | The exact sentence from the abstract; must match verbatim |
| Finding | `outcome_reported` | str | The measure as reported, such as "PHQ-8 mood score" |
| Finding | `effect` | Effect | `increased`, `decreased` or `no_change`, on the outcome as reported |
| Finding | `is_primary` | bool | The abstract names this as its primary outcome |
| Finding | `population` | str | Who was enrolled, as reported |
| Finding | `intervention` | str | Agent, dose, duration, route and form, as reported |
| Finding | `comparator` | str | Placebo, active treatment or usual care, as reported |
| Finding | `dimensions` | dict | One value per custom dimension, keyed by name |
| NormalizedOutcome | `name`, `members`, `higher_is_better`, `reviewed` | str, list[str], bool, bool | Lives in the committed mapping file |
| CandidatePair | `outcome`, `study_a`, `study_b`, `kind` | — | `kind` is `effect_vs_none` or `benefit_vs_harm` |
| Classification | `pair_id`, `verdict`, `reason`, `also_differs`, `detail`, `rationale`, `arm` | — | `verdict` is `genuine` or `apparent`; `detail` is a short specific, such as "duration: 8 weeks vs a median 5.3 years" |

A `pair_id` is the outcome plus the two study keys in canonical order, so a pair has one
identity across runs and arms. The study key comes from the same rule that merges papers from
one trial: a paper with no registration is keyed by its PMID.

Papers merge transitively. If paper A shares a registration with B, and B with C, all three are
one study, even when A and C share none; the studies are the connected components of a graph of
papers joined by shared IDs.

**What the model returns.** Extraction returns an Extraction per abstract: the study-level design
(used only when publication types don't name one), the sample size, a one-sentence summary for
reviews and meta-analyses only, and the list of findings. Code assigns `finding_id`. Stored
Findings keep custom-dimension values in a `dimensions` mapping, so the Finding model stays
static while the extraction model varies with the question.

**Direction is derived, not extracted.** The model reports what the abstract says happened to
the measure (scores went down). The reviewed mapping says whether down is good for that outcome.
Benefit, no effect or harm is computed from the two, so the polarity judgment stays with the
reviewer, in one file.

**Contradiction reasons** are fixed, taken from the Cochrane Handbook's two sources of
heterogeneity: clinical diversity (participants, interventions, outcomes) and methodological
diversity (design, risk of bias) ([Cochrane Handbook 9.5.1][cochrane]). They also match the
PICO framing GRADE uses for indirectness ([GRADE][grade-pico]).

| Reason | Kind | Covers |
| --- | --- | --- |
| Population | Clinical | Who was enrolled |
| Intervention | Clinical | Dose, duration, route, form, co-treatment |
| Comparator | Clinical | Placebo, active treatment, usual care |
| Outcome measure | Clinical or methodological | Definition, instrument or timing, within one normalized outcome |
| Design or risk of bias | Methodological | Study type; bias the abstract reports, such as open-label or high dropout |
| Unclear | — | None of the above fits |

**Custom dimensions** let an expert extend the frame without replacing it. Each is defined in the
question config with a name, a one-line definition, a type (text, a fixed list of options, or a
number with its unit) and the reason it refines. The extraction model is generated from the
config, so each dimension is a validated field. Dimensions refine reasons and never add top-level
ones. A question has at most five, which keeps the schema small enough to extract well. A
dimension counts as evaluated only if it is labeled; an unlabeled one shows "not evaluated" on
the page.

Every dimension has exactly one way to say the abstract doesn't report it. An options dimension
must list `not_reported`, and the model must pick an option, never null. Text and number
dimensions use null. The page shows "Not reported" for both.

v1 ships one dimension: baseline vitamin D status, refining population, with the options
`below_20_ng_ml` (25(OH)D strictly below 20 ng/mL, 50 nmol/L), `at_or_above_20_ng_ml`, `mixed`
and `not_reported`. The 20 ng/mL cut-off is the deficiency threshold in the Endocrine Society's 2011
guideline ([Holick et al. 2011][endo-2011]); its 2024 guideline endorses no thresholds
([Demay et al. 2024][endo-2024]), so the cut-off only groups studies.

Exactly 20 ng/mL (50 nmol/L) is `at_or_above_20_ng_ml` everywhere; `below_20_ng_ml` means
strictly below. Dimensions are per finding, so a subgroup finding carries its own band. The band
follows a fixed precedence, written into the dimension's definition and used again in the
labeling guide:

1. the population's eligibility restriction, if it was restricted by vitamin D status and its
   whole range is strictly below 20 ng/mL (50 nmol/L) or at or above it, in either unit. A
   restriction with values on both sides, such as ≤20 ng/mL, ≤50 nmol/L or <30 ng/mL, doesn't
   settle the band and falls through to the next rule;
2. otherwise, the baseline mean or median the abstract reports for that population;
3. `mixed` only when the abstract reports values on both sides of 20 ng/mL with no single summary
   for that finding's population;
4. otherwise `not_reported`.

A fixed order keeps the model and the labels from resolving the same abstract differently, so the
eval measures extraction rather than disagreement over the rule.

**Design weights.** The summary strip weights studies by design.

| Design | Weight | In the strip |
| --- | --- | --- |
| Randomized controlled trial | 3 | Counted |
| Non-randomized controlled trial | 2 | Counted |
| Cohort | 2 | Counted |
| Case-control, cross-sectional | 1 | Counted |
| Case series | 0.5 | Counted |
| Other (fits none of the above) | — | Shown, not counted |
| Systematic review, meta-analysis | — | Listed, not counted |
| Case report, animal, in vitro | — | Excluded by default; set per question |

## Retrieve and extract

Retrieval is plain, polite HTTP; extraction is one validated model call per abstract, with every
quote checked against its source.

**Question config.** `questions/<id>.toml` holds the id, title, topic terms, a list of filters
in PubMed syntax joined to the topic query with AND, a fixed date range, `exclude_designs` and
the custom dimensions. The date range has a fixed end date so reruns cannot drift. The config is
committed, so every run is reproducible.

**Topic scope.** The config does not hold a hand-written query. It lists `exposure_terms` and
`outcome_terms`, and `topic_match` says where they must appear: `title` or `title_abstract`.
The loader builds the topic query as any exposure term AND any outcome term, each tagged with
that field; a trailing `*` truncates, and every other term is searched as a quoted phrase.
Scope is a config choice, not code, because it trades recall against review effort: title
scope finds the studies that are about the question, while title-and-abstract scope also finds
studies that report the outcome secondarily.

**Retrieve.**

- ESearch with `usehistory=y`, then EFetch in batches of 200 PMIDs as XML. A token-bucket limiter
  holds 3 requests per second, or 10 when `NCBI_API_KEY` is set. Every request names the tool and
  a contact email, as NCBI's usage guidelines ask. Rate-limit responses, server errors and network
  failures are retried up to three times with backoff.
- The date range bounds the Entrez date (`[edat]`), the day a record was added to PubMed, not the
  publication date. That freezes the result set: a record published before the end date but
  indexed after it cannot appear on a later rerun. The page states the range as "records added to
  PubMed through October 31, 2026".
- ESearch returns at most 10,000 PMIDs for PubMed. A larger result stops the run and asks for a
  narrower query, rather than silently truncating it.
- Raw XML is cached per PMID, so reruns make no requests. The parser keeps PMID, title, first
  author, journal, labelled abstract sections, year, article language, publication types, MeSH
  headings and trial IDs.
  - The first author is the first listed surname, or the group name for a group author.
  - The year is the issue's publication year, falling back to MEDLINE's free-text date, then the
    electronic publication date, then the date the record entered PubMed.
  - Trial IDs come from the record's links to any of the trial registries NLM lists
    ([NLM: DataBank sources][nlm-databank]), such as ClinicalTrials.gov, IRCT, ChiCTR or ISRCTN,
    plus any NCT number written in the title or abstract. Each is stored trimmed and upper-cased
    as `REGISTRY:ACCESSION`, such as `CLINICALTRIALS.GOV:NCT01169259`. Links to data
    repositories, such as figshare, are not registrations. In the vitamin D corpus, about a
    quarter of linked registrations are outside ClinicalTrials.gov.
- Extraction reads the English `Abstract` element. Records with no abstract, or with only a
  non-English `OtherAbstract`, are dropped and counted by language. An article in another
  language with an English abstract is kept, with its language recorded.
- Records typed "Retracted Publication" are dropped and counted, and the page's method section
  says how many were excluded. Retraction status is only as current as the cached record. Book
  records are dropped too.
- Each run writes `runs/<id>/`: `search.json` (the exact term, the search date and the PMIDs),
  `raw/<pmid>.xml`, `abstracts.jsonl` and `manifest.json`. A rerun with the same term reuses the
  saved search and the cached records and makes no requests; a changed query or filter searches
  again and fetches only records not already cached. Files are written whole or not at all, so
  an interrupted run resumes by fetching just the missing PMIDs. A PMID the search lists but
  EFetch never returns is recorded as unavailable and retried only on `--refresh`.
- `--refresh` searches and re-fetches everything. A published run refreshes first, so retraction
  status is current.
- The run manifest records the query, the date range in words, the search date and the counts:
  found, fetched, cached, unavailable, kept, and dropped by reason. Its language report counts
  kept records by article language and dropped records by reason and language, so the evidence
  outside English is visible rather than silent. It also records the requests made and the raw
  cache size.

**Extract.**

- One request per abstract through the batch API, using tool-use structured output. The
  extraction model is built from the fixed fields plus the config's custom dimensions; the tool's
  input schema is generated from that model, and the tool input is validated against it. Invalid
  output gets one retry with the validation error; a second failure is logged and counted.
- The static instructions and schema form a cached prompt prefix. Cache reads cost 0.1× the input
  price on Haiku 5.5 and 0.05× on Sonnet 5.5 and Opus 5.5, and they stack with the batch
  discount ([pricing][pricing]).
- **Verbatim check.** Each quote must appear in its abstract after whitespace and quote-mark
  normalization. A miss gets one retry that names the miss; a second miss drops the finding and
  counts it. The page never shows an unchecked quote.
- Custom dimensions take their definition from the config. Operational criteria belong in the
  definition. Number dimensions name one unit and the model converts to it.
- **Outcome domain.** The config names the outcomes the question is about; for vitamin D,
  depressive symptoms, depression diagnosis or incidence, remission and response. Extraction
  returns only findings in that domain. Findings on other outcomes are counted as out of scope
  in the run manifest and not stored, so a broad search adds recall without filling the map with
  unrelated outcomes.
- Reviews and meta-analyses get study fields and a one-sentence summary for the reviews list,
  labelled as a summary. Their findings are not extracted for counting.
- **Model.** Extraction starts on the small model and moves to the mid-tier model only where the
  extraction eval shows a field-level gap.

## Normalize

Normalization is the load-bearing step: one model proposal over the unique outcome strings, then
a person reviews the mapping before anything is published.

- Collect the unique `outcome_reported` strings across all findings. We expect a few hundred for
  one question.
- One model call proposes groups: a canonical name, the member strings and `higher_is_better`.
  The result is written to `mappings/<id>.toml` with `reviewed = false`. The file is TOML,
  committed and edited by hand.
- The hosted build refuses an unreviewed mapping. The CLI demo may run unreviewed and labels its
  output so.
- Reruns keep reviewed groups and propose only strings the mapping has not seen, so review effort
  does not repeat.
- Hard cases: "depressive symptoms", "depression" and "PHQ-9 score" merge; "depression
  incidence" and "depression severity" do not. VITAL-DEP reports both incidence and PHQ-8 scores,
  so one trial exercises the rule ([Okereke et al. 2020][vital-dep]).
- The eval scores the model's proposal before review, so it measures the model rather than the
  reviewer.

## Compare and classify

Candidate pairs come from the extracted fields with no model involved; the model only classifies
them, at whatever depth the run asks for.

**Candidates.**

- A pair is two studies on the same normalized outcome whose directions differ: effect vs no
  effect, or benefit vs harm.
- Each study enters a pair through its primary finding on that outcome, so pairs grow with
  studies, not findings. Subgroup findings show in the finding detail but are not paired in v1.
- Pairs within one trial (same PMID or a shared trial ID) and review findings are skipped.
- Every run prints and stores the block size per outcome.

**Classification.**

- Input: both findings' claim, quote, population, intervention, comparator, measure as reported,
  design, sample size and custom dimensions. Output: a Classification with `genuine` or
  `apparent`, a primary reason from the fixed set, any `also_differs` reasons, a short `detail`
  and a one-sentence rationale.
- `--depth` picks one arm of the council ablation, and arm names double as depth names.

| Arm | What runs per pair |
| --- | --- |
| `single-small` | Haiku 5.5, one sample |
| `single-mid` | Sonnet 5.5, one sample |
| `self-consistency` | Sonnet 5.5, k samples, majority vote; k set so tokens match `council-ranked` |
| `self-moa` | Sonnet 5.5, k samples, then Sonnet 5.5 synthesizes them |
| `council` | One mid-tier model from each of three vendors answers; a chair model synthesizes |
| `council-ranked` | As `council`, with anonymized peer ranking before synthesis, following [llm-council][llm-council] by Andrej Karpathy |

Claude models are the default everywhere outside the council arms. The council's other vendors
and models are chosen after a price check, and their SDKs live only in `arms.py` behind an
optional `council` dependency group.

**Dry run.** `--dry-run` runs retrieval, extraction and normalization, then stops before
classification. It prints pairs by outcome and the estimated cost per arm: pairs × mean tokens per
pair, measured on the dev set, × the prices in a dated `prices.toml`.

**Panel.** Genuine contradictions are grouped by reason and ordered by the two studies' combined
design weight, then combined sample size. Apparent contradictions sit in a collapsed list under
the panel, each with the classifier's one-line reason, such as "underpowered: n = 40".

## Summary and graph

The summary strip counts studies per normalized outcome; the graph holds the same data for the
page to draw.

- **Unit:** studies, per normalized outcome. A study's direction on an outcome is its primary
  finding's; failing that, its first-reported finding on that outcome. Papers linked through any
  shared trial registration count once.
- **Weighted share:** the sum of design weights per direction, divided by the total.
- **Sensitivity line:** recompute with every study weighted 1, and with weight = sample size, a
  stand-in for inverse-variance weighting when abstracts give no effect sizes. The strip says
  when either changes the leading direction.
- **Thin outcomes:** under 5 studies, the strip shows counts only, without the weighted bar or the
  sensitivity line, and says the evidence is thin.
- **Reviews and meta-analyses:** listed, not counted.
- **Graph:** a NetworkX graph whose nodes are outcome hubs and findings. Edges link each finding
  to its outcome, plus genuine contradictions with their reason. It is exported as node-link JSON
  with a per-outcome summary block, and a JSON schema test guards the export.
- **Layout** is computed deterministically in the browser, so the export carries data, not
  coordinates. NetworkX holds the graph model and the export; it does no layout or analytics here.

## Page

- **Outcome tabs** switch the strip, the graph or list, the selected finding and the
  contradictions. Reviews and the method section stay fixed. Everything from the strip down sits
  on a lighter band, which the selected tab opens into.
- **Graph or list.** The list is a table grouped by direction, showing study, measured as, design,
  sample size and year. It is the accessible path and the default under 720 px wide.
- **Selected finding:** a fixed-height card beside the graph. Its title and citation are pinned at
  the top and its actions at the bottom; only the middle scrolls. It shows the claim, the verbatim
  quote, direction, outcome and measure as reported, population, intervention, comparator, design,
  sample size, year and each custom dimension, marked "not evaluated" when it has no labels.
- **Weighted bar:** segment percentages on hover.
- **Contradiction panel:** grouped and filterable by reason. Each pair shows its reason and
  `detail`. Apparent contradictions sit in a collapsed list beneath.
- **Pair page:** the two findings side by side, with a row per reason field and per custom
  dimension, marking which differ and which is the likely reason.
- **Empty and thin states:** an outcome with no genuine contradictions says so and still lists
  any apparent ones; a thin outcome shows counts only. An unreviewed mapping is never hosted.
- **Footer:** "Research navigation demo, not medical advice."
- **Accessibility:** every control is a real button or link, and text meets 4.5:1 contrast.
  Direction is never shown by colour alone; position and labels carry it too. The three direction
  colours get a colour-blindness simulation. A keyboard reaches the tabs, the list, the finding
  card and the pairs.
- **Stack:** React and TypeScript on Vite, built to static files on GitHub Pages, reading one JSON
  file per question. D3 supplies only geometry, such as scales and arc paths; React renders the
  page.
- **Theme:** band colour cool grey #EEF1F4 and tab accent teal #0E7C7B, kept clear of the blue
  and orange that mean benefit and harm.

## Evals

Each stage has its own eval on committed gold data. All of them run in CI from recorded
responses, and the README results table is generated from their output, never typed by hand. Gold
data is labeled by hand, from openly licensed articles only.

| Eval | Gold data | Size | Metrics | In CI |
| --- | --- | --- | --- | --- |
| Extraction | Hand-labeled abstracts, including the baseline vitamin D dimension | 50 | Field-level precision and recall for direction, outcome, sample size, design and each labeled dimension; verbatim-quote rate; whether the quote supports the claim; how often design falls to Other | Replay |
| Normalization | Labeled outcome-string pairs, merge or not, including the hard cases | About 200 pairs | Merge precision and recall | Replay |
| Contradictions | Labeled candidate pairs, genuine or apparent, with reason | About 150 pairs, stratified by reason | Precision on genuine; reason accuracy with a confusion matrix | Replay |
| Council ablation | The contradiction set | About 150 pairs | Accuracy and cost per pair for each arm, with paired-bootstrap 95% intervals | Replay; live runs by hand |
| Cost per run | The run manifest's token counts × `prices.toml` | Every run | Dollars per stage | Computed |

- **Interval width:** at 150 pairs and about 80% accuracy, each arm's 95% interval is roughly ±6
  points. Arms are compared on the same pairs, so the paired bootstrap gives tighter intervals on
  their differences. The README states the width so close results aren't over-read.
- **Reason classes:** six classes leave about 25 labeled pairs each, so per-class accuracy is
  reported with its interval, not as a headline.
- **Replay cache:** responses are keyed by a hash of model, parameters and request. CI fails on a
  missing response rather than calling out.
- **Leakage guard:** gold abstracts never appear as prompt examples, and the normalization prompt
  never sees labeled pairs.
- **The ablation's question:** does multi-model deliberation beat the best single model at the
  same token budget? Prior work suggests ensembling, not deliberation, drives most of the gain.
  Either result goes in the README, and the table doubles as the guide to `--depth`.
- **Related work:** Evidence Inference 2.0 ([DeYoung et al. 2020][evinf]) is human-labeled, with
  MIT-licensed annotations, and covers the effect-and-quote task; it is a candidate external
  benchmark. Contradiction detection over biomedical claims is studied by
  [Sosa et al. 2023][sosa], [Alamri and Stevenson 2016][alamri] and [BioDivergence][biodivergence].

## Cost

Pair count, not price, sets the run cost, so the main levers control pairs and the depth spent on
each.

| Lever | Effect |
| --- | --- |
| 50-abstract dev set for tuning | Schema iterations cost cents |
| Batch API | 50% off input and output, including cache reads |
| Cached prompt prefix | Cache reads at 0.1× the input price on Haiku 5.5, 0.05× on Sonnet 5.5 and Opus 5.5 |
| Replay cache | Reruns and CI cost nothing |
| Study-level pairs, differing directions only | Pairs grow with studies, not findings |
| Small model for the bulk of pairs | Larger arms run only where the ablation shows they pay |
| Dry run before spending | Cost is known before it is incurred |

Batch prices in November 2026, per million input/output tokens: Haiku 5.5 $0.05/$0.25, Sonnet 5.5
$1/$5, Opus 5.5 $2/$10 ([pricing][pricing]). Before measurement, we estimate extraction of about
500 abstracts at roughly $0.08 on Haiku or $1.60 on Sonnet, and classification at roughly $0.07 or
$1.30 per 1,000 pairs. These are estimates; the README's cost table reports measured spend.

## CLI and repository

A small Typer CLI drives every stage, and each pipeline step is one module.

- `claimgraph run QUESTION [--depth ARM] [--dry-run] [--replay | --live]`. QUESTION is a config
  path or id; a config, not free text, keeps runs reproducible.
- `claimgraph mapping check QUESTION` fails when the mapping is unreviewed or a string is
  unmapped.
- `claimgraph eval [extraction | normalization | contradictions | ablation]`
- `claimgraph site QUESTION` writes the page data.

```
claimgraph/
  pyproject.toml  uv.lock  README.md  DESIGN.md  LICENSE
  src/claimgraph/
    models.py  dimensions.py  config.py  retrieve.py  extract.py  normalize.py
    compare.py  arms.py  summary.py  graph.py  llm.py  prices.py  cli.py
  questions/   vitamin-d-depression.toml  creatine-cognition.toml
  mappings/    vitamin-d-depression.toml
  evals/       gold/  run.py  results/
  replay/      recorded model responses used by CI
  site/        React + TypeScript front end
  tests/
  .github/workflows/   ci.yml  pages.yml
```

Tooling: uv, ruff, pyright (strict on `src/`), pytest, Pydantic v2, Typer, httpx for
E-utilities and the Anthropic SDK, on Python 3.12.

## Scaling limits and failure modes

The first limit at 10× the corpus is classification spend on mid-tier arms; the first failure
that reaches readers is mis-normalization.

### Scaling limits

These are expectations, to be replaced with measurements.

- **Pairs** grow with the product of direction counts within an outcome. At 10× abstracts the
  small arm still fits the budget and the larger arms may not, and the dry run says so before
  spending.
- **Normalization** is one call. Past a few thousand unique strings it needs chunked proposals
  and a merge pass.
- **The graph** stops reading as a picture once a hub holds several hundred findings. The list
  view carries those outcomes.
- **Review effort** scales with new outcome strings, not with runs, because the mapping is
  incremental.
- **Custom dimensions** each add extraction load and error; the cap of five keeps the schema
  small.
- **E-utilities** is not the limit: 10 requests per second fetches tens of thousands of abstracts
  an hour.

### Failure modes

- **Spin:** abstracts can overstate secondary or subgroup results. Counting primary findings and
  showing every quote limits the damage.
- **Absence of evidence read as evidence of absence:** small trials report "no significant
  difference." Sample size shows on every finding, the size-aware line tests the weighting, and
  the classifier can mark such pairs apparent as underpowered.
- **Mis-normalized outcomes:** over-merging, under-merging, or a polarity error that flips benefit
  and harm. The reviewed mapping and the normalization eval guard this.
- **Design misclassification:** wrong weights. Publication types come first, and design is scored
  in the extraction eval.
- **Double counting:** one trial can publish several papers, and large trials publish many
  secondary analyses. The trial-ID merge handles it.
- **A real quote that doesn't support the claim:** the extraction eval scores support, not just
  presence.
- **The classifier leaning on one reason:** the confusion matrix shows it, and `also_differs`
  keeps the other differences visible.
- **A badly defined custom dimension:** vague definitions extract inconsistently. Operational
  criteria in the definition and the "not evaluated" marker limit the damage.
- **Evidence outside English:** records with only a non-English abstract are dropped and counted
  by language, so the gap is visible rather than silent.
- **Query drift:** the PubMed query is committed and the count recorded per run.

## Alternatives rejected

| Alternative | Why not |
| --- | --- |
| Streamlit or a notebook front end | Reads as a prototype |
| All-pairs comparison | About 500,000 pairs at 1,000 claims |
| An agent framework (such as LangGraph) for normalization | The only loop would tune on the eval's test set; every flow here is fixed |
| Full-text scraping | Licensing exposure |
| Live queries on the hosted site | Hosting cost and abuse surface; static is enough |
| An MCP server in v1 | Out of scope; nothing here needs a live server |
| A home-made reason taxonomy | Cochrane's sources of heterogeneity already cover it, and reviewers recognize them |
| A reason set users can extend | Breaks the reason eval and comparability across pages; custom dimensions refine reasons instead |
| The model writes benefit or harm directly | Hides the polarity judgment inside thousands of outputs |
| Pairing every finding | Multiplies pairs without changing what the panel shows |
| Embedding similarity to choose pairs or merge outcomes | Needs a similarity threshold nobody can defend, and still needs review |
| Mapping outcomes to MeSH or UMLS | UMLS needs its own licence, and outcome instruments are poorly covered |
| Cytoscape.js | The layout is custom; React with D3 geometry gives direct control |

## Measured numbers

None yet. Counts, block sizes, token use, cost and eval scores land here as they are measured.

## References

- [NLM: E-utilities rate limits][nlm-limits]
- [NCBI: E-utilities usage guidelines][ncbi-guidelines]
- [NLM: copyright and downloading data][nlm-copyright]
- [NLM: MEDLINE citations by language][nlm-lang]
- [PubMed DTD: OtherAbstract][dtd-other]
- [NLM: MEDLINE DataBank sources and trial registries][nlm-databank]
- [Claude API pricing][pricing]
- [Cochrane Handbook 5.1, section 9.5.1: what is heterogeneity][cochrane]
- [GRADE: indirectness and PICO][grade-pico]
- [GRADE certainty criteria, CDC ACIP handbook][grade-cdc]
- [Okereke et al. 2020, VITAL-DEP][vital-dep]
- [Ghaemi et al. 2024, vitamin D dose-response meta-analysis][ghaemi]
- [Anglin et al. 2013, vitamin D deficiency and depression][anglin]
- [Holick et al. 2011, Endocrine Society guideline on vitamin D deficiency][endo-2011]
- [Demay et al. 2024, Endocrine Society guideline on vitamin D for disease prevention][endo-2024]
- [Xu et al. 2024, creatine and cognition][xu]
- [DeYoung et al. 2020, Evidence Inference 2.0][evinf]
- [Sosa et al. 2023, contradictory COVID-19 drug claims][sosa]
- [Alamri and Stevenson 2016, contradictory cardiovascular claims][alamri]
- [BioDivergence, hidden contextual contradictions][biodivergence]
- [llm-council][llm-council]

[nlm-limits]: https://support.nlm.nih.gov/kbArticle/?pn=KA-05510
[ncbi-guidelines]: https://www.ncbi.nlm.nih.gov/books/NBK25497/
[nlm-copyright]: https://www.nlm.nih.gov/databases/download.html
[nlm-lang]: https://www.nlm.nih.gov/bsd/medline_lang_distr.html
[dtd-other]: https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/240101/el-OtherAbstract.html
[nlm-databank]: https://www.nlm.nih.gov/bsd/medline_databank_source.html
[pricing]: https://platform.claude.com/docs/en/about-claude/pricing
[cochrane]: https://training-noproxy.cochrane.org/handbook/archive/v5.1/chapter_9/9_5_1_what_is_heterogeneity.htm
[grade-pico]: https://scholarworks.aub.edu.lb/items/3ff7d4a6-32f2-46d7-9ffe-15425f8d58fd
[grade-cdc]: https://www.cdc.gov/acip-grade-handbook/hcp/chapter-7-grade-criteria-determining-certainty-of-evidence/index.html
[vital-dep]: https://pmc.ncbi.nlm.nih.gov/articles/PMC7403921/
[ghaemi]: https://core-prod.cambridgecore.org/core/product/8F18452740B621CC04F441F037A2513B/core-reader
[anglin]: https://core-prod.cambridgecore.org/core/product/F4E7DFBE5A7B99C9E6430AF472286860/core-reader
[endo-2011]: https://doi.org/10.1210/jc.2011-0385
[endo-2024]: https://doi.org/10.1210/clinem/dgae290
[xu]: https://pmc.ncbi.nlm.nih.gov/articles/PMC11275561/
[evinf]: https://arxiv.org/abs/2005.04177
[sosa]: https://arxiv.org/abs/2212.09867
[alamri]: https://pmc.ncbi.nlm.nih.gov/articles/PMC4897929/
[biodivergence]: https://arxiv.org/abs/2606.11208
[llm-council]: https://github.com/karpathy/llm-council
