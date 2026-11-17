# Claimgraph design

Claimgraph turns the PubMed abstracts on one health question, 1,901 kept for vitamin D and
depression, into a contradiction map a general reader can trust, for under $15 a run. This file
records the problem, the decisions and the alternatives rejected, the limits we expect, and the
numbers we measure. It grows with the code.

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
  quotes single sentences with links, and committed abstract text comes only from articles under
  CC BY or CC0. The rule governs what is committed: the dev sample may use any licence because
  only its PMIDs are committed.
- **Language.** 97% of MEDLINE citations from 2020–2023 are English-language articles
  ([NLM][nlm-lang]). Abstracts in other languages sit in PubMed's `OtherAbstract` element
  ([DTD][dtd-other]). v1 extracts from the English abstract.
- **Budget.** $30–100 all in; one full run under $15. It was set for about 500 abstracts; the
  vitamin D corpus kept 1,901.
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
| 1. Retrieve | `questions/<id>.toml` | `runs/<id>/search.json`, `raw/<pmid>.xml`, `abstracts.jsonl`, `manifest.json` | Deterministic | PMID |
| 2. Extract | abstracts | `runs/<id>/extract/<sample>-<model>.jsonl`, `extract/manifest.json` | Model, validated by Pydantic | Request hash |
| 3. Normalize | unique outcome strings | `mappings/<id>.toml` (committed) | Model proposes, a person reviews | The mapping file |
| 4. Compare | findings and mapping | `runs/<id>/compare/candidates.json`, `classify/<split>-<arm>.jsonl`, `classify/contradictions.json` | Pairs deterministic; model classifies | Request hash |
| 5. Build graph | all of the above | the export JSON, published as a release asset, never committed | Deterministic | — |
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
| Study | `design` | Design | From PubMed publication types when they name it, though Review alone names none; the model otherwise |
| Study | `sample_size` | int or None | Enrolled or randomized, not analyzed |
| Finding | `finding_id` | str | `<pmid>-<n>`, assigned by code |
| Finding | `claim` | str | One plain sentence written by the model |
| Finding | `quote` | str | The exact sentence from the abstract; must match verbatim |
| Finding | `outcome_reported` | str | The measure as reported, such as "PHQ-8 mood score" |
| Finding | `effect` | Effect | `increased`, `decreased` or `no_change`, on the side with more of the exposure; worked out by code from the next two |
| Finding | `exposure_side` | ExposureSide | `more` or `less`: the side the abstract's sentence is worded from, reported by the model |
| Finding | `outcome_direction` | Effect | What happened to the outcome on that side, reported by the model |
| Finding | `comparison` | Comparison | `between_groups_or_across_participants`, `within_one_group` or `pooled_across_groups` |
| Finding | `is_primary` | bool | The abstract names this as its primary outcome |
| Finding | `is_subgroup` | bool | The result covers only part of the analyzed sample, split by a characteristic such as sex, age or baseline status |
| Finding | `subgroup` | str or None | For a subgroup result, which part of the sample, as the abstract names it |
| Finding | `population` | str | Who was enrolled, as reported |
| Finding | `intervention` | str | Agent, dose, duration, route and form, as reported |
| Finding | `comparator` | str | Placebo, active treatment or usual care, as reported |
| Finding | `domain_outcome` | str | The entry of the question's outcome domain the finding is on |
| Finding | `dimensions` | dict | One value per custom dimension, keyed by name |
| NormalizedOutcome | `name`, `members`, `higher_is_better`, `reviewed` | str, list[str], bool, bool | Lives in the committed mapping file |
| PolarityOverride | `string`, `higher_is_better`, `reviewed`, `why` | str, bool, bool, str | A member string scored the other way from its outcome; in the mapping file |
| ExcludedFinding | `finding_id`, `reason`, `reviewed`, `why` | str, enum, bool, str | One finding the review leaves out; `reason` is `direction_not_reported`; in the mapping file |
| CandidatePair | `outcome`, `study_a`, `study_b`, `kind` | — | `kind` is `effect_vs_none` or `benefit_vs_harm` |
| Classification | `pair_id`, `verdict`, `reason`, `also_differs`, `detail`, `rationale`, `arm` | — | `verdict` is `genuine`, `apparent` or `off_question`, which takes no reason; `detail` is a short specific, such as "duration: 8 weeks vs a median 5.3 years" |

A `pair_id` is the outcome plus the two study keys in canonical order, so a pair has one
identity across runs and arms. The study key comes from the same rule that merges papers from
one trial: a paper with no registration is keyed by its PMID.

Papers merge transitively. If paper A shares a registration with B, and B with C, all three are
one study, even when A and C share none; the studies are the connected components of a graph of
papers joined by shared IDs.

**What the model returns.** Extraction returns an Extraction per abstract: answers to the design
questions, the sample size, a one-sentence summary for reviews only, systematic, meta-analyses
or narrative, the other outcomes it saw, and the list of findings.

- **Design answers.** The model answers the same design questions the labels use, every one of
  them for every abstract, each on its own facts: whether a study pools results is simply no for
  a trial. Code walks the tree from the first question and ignores the answers off the path, so
  the model gives only facts and all the path logic is in code. The design is used only when
  publication types don't name one. With thinking off, answering the questions is how the model
  applies the guide's definitions. In gold the design itself is the label: answers stored with
  a label are provenance, never required or scored. When the model's design is wrong, the eval
  diagnoses it from the model's own answers, as the first question where its path leaves the
  gold design's.
- **Why facts, not a path.** The first live run asked for path-aware answers: the model followed
  the tree itself and marked the questions it never reached `not_reached`. Only 33 of 50 dev
  and 22 of 32 gold responses were valid on the first try, and every error was a design-path
  error: a question on the model's own path marked `not_reached`, most often the genetic-proxy
  question after "the researchers did not give the exposure", or the first question on a
  review. Answering every question on its facts replaced it, so that class of error cannot
  occur; the retry stays for other errors.
- **Effect from two facts.** The model does not give the effect. It reports the side the
  sentence is worded from, more or less of the exposure, and what happened to the outcome on
  that side, and code turns a result worded from less round. "Deficiency was linked to more
  depression" is less and increased, so the effect is decreased. Two prompt wordings that asked
  the model to turn such results round itself still left it reading deficiency the wrong way
  on 2 dev abstracts each time, and a flipped direction makes a false contradiction, the
  costliest error the extraction can make. In gold the effect stays the label; the two facts
  are provenance, never labeled or scored, as the design answers are.
- **Comparison as a fact.** Each finding says what it compares: groups with each other or an
  association across participants, a change within one group, or a change pooled across the
  groups. Code drops the last two only when the design, as code works it out, is an RCT or a
  non-randomized trial, whose findings are its comparisons between arms; a case series keeps
  after against before, and observational designs never drop on this field. The kept choice
  is named so a correlation across participants plainly fits it. A prompt rule against
  within-group change still left 3 dev trials reporting one. Dropped findings are counted in
  the extract manifest.
- **Other outcomes** are the outcomes outside the domain that the abstract reports results for.
  They are counted as out of scope in the run manifest, and never scored, labeled or stored as
  findings.
- **Domain outcome.** Each finding names the outcome-domain entry it is on, or the explicit
  choice "outside the domain". Code drops a finding marked outside and adds its outcome to the
  other outcomes, so it is counted, not stored. Without that way out, the model would have to
  file an anxiety finding under a depression entry, which hides the error instead of removing
  it. The first scored gold run's dev set showed the need: 20 of 73 dev findings were on
  outcomes outside the domain, such as anxiety, fatigue or cognition, or gave the exposure's
  own level as the outcome, which the prompt now rules out.

Code assigns `finding_id`. Stored Findings keep custom-dimension values in a `dimensions`
mapping, so the Finding model stays static while the extraction model varies with the question.

**Direction is derived, not extracted.** The model reports what the abstract says happened to
the measure (scores went down). The reviewed mapping says whether down is good for that outcome.
Benefit, no effect or harm is computed from the two, so the polarity judgment stays with the
reviewer, in one file. Polarity is per outcome by default, with reviewed exceptions for strings
whose instrument is scored the other way.

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
| Other (a primary study outside the designs above, such as quasi-experimental, Mendelian randomization or unclear) | — | Shown, not counted |
| Systematic review, meta-analysis | — | Listed, not counted |
| Narrative review | — | Not counted |
| Case report, animal, in vitro | — | Excluded by default; set per question |

Narrative reviews, reviews without systematic methods, have their own design rather than falling
to Other. Other then holds only primary studies outside the weighted designs, so how often
extraction uses it stays a clean signal in the extraction eval.

**Trials need a comparison group.** A non-randomized trial allocates people to different
interventions by methods that are not random ([EPOC][epoc-designs]), so it needs a concurrent
comparison group. A single-arm study, where everyone gets vitamin D and outcomes are compared
before and after, is a case series, a series of patients given similar treatment
([CEBM][cebm-glossary]), and weighs 0.5; its effect is the change from before to after. A
comparison with historical controls only is a case series too: it has no concurrent group
either, and historical controls are long known to inflate the apparent benefit of new
treatments ([Goodacre 2015][goodacre]). Extraction reports how many kept records are single-arm.

**Design from publication types.** Only a review tag sets the design: Meta-Analysis, then
Systematic Review or Scoping Review, a scoping review having systematic methods without
pooling. The model's design answers set every other design, a tagged trial's included,
because PubMed also tags observational secondary analyses of trial participants Randomized
Controlled Trial, and the tag would give them trial weight. The extraction eval chose this
over setting the trial tag first too (see Failure modes). The model's design answers are kept
either way, so a disagreement with the tag stays visible. Review alone
names no design: the model decides from the abstract whether the review is systematic or
narrative. Among the kept records, at least 21 tagged only Review are systematic reviews,
meta-analyses or scoping reviews by their own title or stated methods, while 1 of 109 tagged
Systematic Review or Meta-Analysis describes no method (see Measured numbers).

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

In title-and-abstract scope, optional `exposure_mesh` and `outcome_mesh` lists add MeSH headings
to each side, `OR`-ed with the terms and searched with `[mh]`, which also matches the narrower
headings beneath each one. Combining controlled vocabulary with free text is standard practice
for systematic searches ([Cochrane Handbook, section 4.4.4][cochrane-search]): indexers assign
headings from the full article, so they find records whose abstract words miss the topic, while
free text finds records not yet indexed. Title scope ignores the headings and stays a test of the
title alone.

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
- Each run searches both topic scopes and fetches only the configured one. It writes
  `runs/<id>/`: `search.json` (the search date and, per scope, the exact term, count and PMIDs),
  `raw/<pmid>.xml`, `abstracts.jsonl` and `manifest.json`. Title scope's records are a subset of
  title-and-abstract scope's, so a broad cache holds both and scope can be compared on measured
  pairs and cost without another fetch. A rerun with the same term reuses the
  saved search and the cached records and makes no requests; a changed query or filter searches
  again and fetches only records not already cached. Files are written whole or not at all, so
  an interrupted run resumes by fetching just the missing PMIDs. A PMID the search lists but
  EFetch never returns is recorded as unavailable and retried only on `--refresh`.
- `--refresh` searches and re-fetches everything. A published run refreshes first, so retraction
  status is current.
- The run manifest records the scope, its query, both scopes' counts, the date range in words,
  the search date and the counts:
  found, fetched, cached, unavailable, kept, and dropped by reason. Its language report counts
  kept records by article language and dropped records by reason and language, so the evidence
  outside English is visible rather than silent. It also records the requests made and the raw
  cache size.

**Extract.**

- One request per abstract through the batch API, using structured output: the response is JSON
  that must follow a schema. A forced tool call would do the same job, but Sonnet 5.5 rejects
  forcing one, and an unforced call can be skipped. The extraction model is built from the fixed
  fields plus the config's custom dimensions, and the schema is generated from that model.
- **Schema limits.** The API enforces only part of JSON Schema, so keywords it ignores, such as
  `pattern` and `exclusiveMinimum`, are left out of what is sent. A check fails before any
  request when a schema breaks the API's limits: an object open to extra properties, more than
  24 optional parameters, or more than 16 union-type parameters, a nullable field counting as
  one. The largest config the loader allows, five text or number dimensions, stays inside them.
- **Validation.** Every response is validated against the extraction model, whatever the API
  enforced. Closed values match whatever their case. A response that stops for `refusal` or
  `max_tokens` is invalid. Invalid output gets one retry: the request again, with the invalid
  output and a note naming what was wrong. A second failure is logged and counted.
- **Thinking off.** Extraction runs without thinking, at a fixed effort, the same on both
  models. Haiku 5.5 takes `disabled`; Sonnet 5.5 rejects it, so it takes `between_tools`, its
  lowest setting, which gives text only when there are no tools. The settings stay fixed per
  model because changing them invalidates the cached prefix.
- The static instructions and schema form a cached prompt prefix: the system prompt carries the
  cache marker and the abstract follows it. Cache reads cost 0.1× the input price on Haiku 5.5
  and 0.05× on Sonnet 5.5 and Opus 5.5, and they stack with the batch discount
  ([pricing][pricing]).
- **Batches in rounds.** An abstract's retries each depend on the response before them, so a
  batch run goes in rounds: each round replays every abstract as far as the recordings go,
  sends the requests that come next in one batch, and records the answers. Three rounds cover
  the first request, the retry for invalid output and the retry for missed quotes. A request
  the batch fails, errored or expired, is reported and not sent again in that run. Batch
  responses are recorded exactly as direct ones, so a run replays the same either way; dev and
  corpus runs share recordings, since a dev abstract makes the same request in both.
- **A stopped run is picked up, not paid twice.** Each batch's id is written to
  `pending-batches.txt` in the recordings directory before the run waits on it, and removed
  once its results are recorded. The next live run collects any batch left there first and
  records each result whose key is a request it needs; the rest are dropped, since a result can
  only be recorded beside the exact request it answers.
- **A first stage before the corpus.** `--limit N` extracts only the first N kept abstracts,
  under its own manifest entry, such as `corpus-first200-sonnet`. The dry run's ceiling assumes
  every request is retried twice at the output limit, so a first stage measures the real cost
  and retry rate before the rest is sent. Its recordings are the corpus run's, so the full run
  replays them and sends only what is left.
- **Verbatim check.** Each quote must appear in its abstract after whitespace and quote-mark
  normalization: curly and straight marks match, and so do runs of spaces and line breaks;
  anything else, a dropped mark or a changed number, is a miss. The abstract's sections are the
  source, not its title. A miss gets one retry that names the missed quotes, separate from the
  retry for invalid output; a second miss drops the finding and counts it, and so does a miss
  whose retry comes back invalid. The quote is stored as the model wrote it; only the check
  normalizes. Each abstract records how many quotes were checked, missed and dropped, for the
  eval's verbatim-quote rate. The page never shows an unchecked quote.
- **One prompt for every question.** The extraction prompt states the labeling rules in
  general terms, filled in from the question config. Examples specific to a question live in its
  config and the labeling guide, never in the shared prompt, so a new question needs no prompt
  edits. The worked examples for the effect's two facts, such as a result stated from
  deficiency, are in general terms too. Read as written, such a result flips its direction and
  makes a false contradiction, the costliest error the extraction can make, so the first
  example went in when dev showed it on two abstracts, without waiting for a pattern on more.
  The rules:
  - The effect is read from the side with more of the exposure.
  - Measures that combine the outcome domain with something else don't count, and neither do
    scales for a neighbouring construct.
  - The quote is the Results sentence that reports the finding, a Conclusion sentence only when
    no Results sentence does, and without headings the sentence that reports the measured
    comparison.
  - The outcome as reported is the measure as the abstract names it, including the instrument
    when the abstract names one; an instrument's name alone is complete.
  - The sample size is the number enrolled or randomized: for a secondary analysis, the number
    entering it; for several samples, their total; the only count, when there is just one; never
    a planned size; for Mendelian randomization, cases plus controls in the main outcome dataset.
  - Time points of one measure that agree are one finding, and ones that disagree are one each.
  - With a comparison group, only comparisons between groups are findings: not a change over
    time within one group, nor one pooled across the groups.
  - Interaction or moderation results are subgroup findings, one per subgroup comparison, null
    ones included. When only the interaction's significance is reported, the abstract's words
    set each subgroup's direction.
  - When the exposure is among the factors tested and a sentence says the rest were not
    significant, that sentence backs a no-change finding.
  - Results from a genetic proxy for the exposure are findings only in a study whose design is
    `other`, such as Mendelian randomization, and only from exposure to outcome. A study that
    also measures the exposure keeps only its measured results, so proxy results are not counted
    under its design.
  - A comparison group got none of the exposure or a different amount; a group that differs only
    by outcome status is not one. A review that says it followed PRISMA has systematic methods.
- Custom dimensions take their definition from the config. Operational criteria belong in the
  definition. Number dimensions name one unit and the model converts to it.
- **Outcome domain.** The config names the outcomes the question is about; for vitamin D,
  depressive symptoms, depression diagnosis or incidence, remission, response and seasonal
  affective disorder. Extraction returns only findings in that domain. Findings on other
  outcomes are counted as out of scope in the extract stage's manifest and not stored, so a
  broad search adds recall without filling the map with unrelated outcomes.
- **Scope is not grouping.** The domain list decides what is in scope; normalization groups by
  what was measured. Seasonal affective disorder is a domain entry so extraction keeps its
  findings, but for normalization it is a population: a severity score in people with it merges
  with depressive symptoms, and a diagnosis or incidence of it with diagnosis or incidence. Who
  was studied is weighed as a population reason when findings disagree.
- **Extract manifest.** Each extraction run writes an entry, such as `dev-sonnet`, to
  `extract/manifest.json` in the run directory: abstracts extracted and missing, output invalid
  after the retry, findings kept, dropped as outside the domain and dropped as within one group
  or pooled in a trial, other outcomes seen,
  primary results kept as subgroups, review findings dropped, single-arm studies, the quote
  counts, the token counts and their cost. Retrieval rewrites `manifest.json` whole, so the
  extract stage keeps its own.
- Reviews, systematic or narrative, and meta-analyses get study fields and a one-sentence
  summary, labelled as a summary. Their findings are not extracted for counting.
- Protocols report no results, so they have no findings; their design is the planned design.
- **Model.** Extraction started on the small model and moved to the mid-tier model where the
  extraction eval showed a gap, on design, which sets each study's weight. It runs on Sonnet 5.5;
  Gold runs gives the reasoning.

## Normalize

Normalization is the load-bearing step: one model proposal over the unique outcome strings, then
a person reviews the mapping before anything is published.

- Collect the unique `outcome_reported` strings across all findings. We expected a few hundred
  for one question; the vitamin D corpus has 547, from 815 findings.
- One model call proposes groups: a canonical name, the member strings and `higher_is_better`.
  The result is written to `mappings/<id>.toml` with `reviewed = false`. The file is TOML,
  committed and edited by hand.
- **The proposal call.** `run --stage normalize` sends the sorted strings, numbered, in one direct
  request on the extraction model's settings. The model defines the outcomes, each with the one
  domain entry it measures, then assigns every string by number, in list order. Numbers, not
  strings, so it cannot misspell a member. Code checks that every number is assigned once, to a
  defined outcome, and that no name repeats; a failure gets one retry naming it, like extraction's.
  The recording is committed under `replay/<id>/normalization/`, so the eval replays the proposal it
  scores; it holds outcome names and no abstract text, the same names the committed mapping lists.
  An existing mapping file is never overwritten. On the 547 vitamin D strings the first proposal was
  valid on the first attempt: 8 outcomes, for $0.15 at standard prices, with 12,900 output tokens.
- **Assign in order, not partition.** The first version asked for each outcome's member
  numbers. On the 547 vitamin D strings both attempts failed the check: 9 strings in two
  outcomes and 1 in none, then 1 in none after the retry. It also merged remission with
  response, two domain entries. Walking the list in order and naming one outcome per string
  keeps the bookkeeping local, and a domain entry per outcome lets the schema keep entries
  apart.
- **What the prompt groups by.** Strings group by what is measured, not how: the instrument,
  scale, cut-off, timing and population a string names stay inside one outcome, where the
  classifier weighs them as an outcome-measure reason. A question's config can carry
  `outcome_rules`, the labeling guide's outcome rules in its own words, and the prompt then
  groups by them, one outcome per kind. Without rules it groups by construct: whether a
  condition occurs and how severe its symptoms are stay apart, and no outcome spans two entries
  of the outcome domain. Either way, a string that measures nothing in the domain goes to an
  outcome marked outside the domain, so the reviewer sees it.
  The prompt keeps each outcome within one domain entry, so it can keep seasonal affective
  disorder apart, while the labels and the mapping review group its strings by what they
  measure. The recorded proposal is scored as it is, so the eval counts those splits as misses.
- **Rules without labeled examples.** The rules' examples are invented, as the extraction
  prompt's are, because the guide's own examples include labeled corpus strings, the named hard
  cases among them, and the eval's hard-case stratum would score a kind the prompt gave away. A
  test checks that no phrase the prompt quotes is a labeled string and that no labeled string of
  three words or more appears in it. Short labeled strings such as "depression" are also rule
  and topic words, so they are checked only where quoted.
- **One string, two domain entries.** A mapping keys on the string alone, so a string that
  findings place in two domain entries goes to one outcome. In the vitamin D corpus that is
  "depression": 17 findings place it under diagnosis or incidence and 10 under symptoms. The
  hard case below puts it with diagnosis or incidence, and the reviewer sees the split.
- **Dropped strings.** The review moves strings that measure nothing in the domain to an
  `out_of_domain` list at the top of the mapping file. They count as mapped, so `mapping check`
  passes and a rerun never proposes them again; later stages leave their findings out and count
  them, as the extract stage counts findings outside the domain.
- **Polarity exceptions.** Polarity is per outcome by default: lower is better for symptom
  scores and case status, higher for remission and response. An instrument scored the other way
  gets a reviewed per-string override in the mapping file, with what its scale means, and
  compare, the summary strip, the graph and the classifier read that string's findings by it.
  Labeling found the first: the 15D, a quality-of-life instrument whose depression dimension
  runs from 0 to 1, 1 being no problem ([Sintonen 2001][sintonen]), was mapped into depressive
  symptoms, so a trial reporting its score rose read as harm. A scan of every mapped string,
  and of every finding that reads as harm on a lower-is-better outcome, found no other
  instrument scored that way; strings worded as a change, such as "decline of depressive
  symptoms", are extracted with the direction of the symptoms, so they read correctly. The
  override moved the 15D trial's headline from harm to benefit and changed 286 of 27,945
  candidate pairs: 178 benefit-against-harm pairs went, 2 came, and 108 kept their kind with
  that side now read as benefit, leaving 27,769.
- **Excluded findings.** A finding whose string is in the domain but whose result cannot be read is
  left out one by one: an `excluded_findings` entry in the mapping file names it, a reason from a
  closed set and what the abstract says. It applies once reviewed; then the finding leaves the
  summary strip, the graph and the pairs, and the method section counts it by reason. The hosted
  build refuses an unreviewed entry. The reasons are `direction_not_reported`, below, and
  `exposure_off_question`, for a finding whose exposure is not the question's, which classification
  flags as `off_question`. The two vitamin D entries, 39051261-1 and 40596345-1, are each their
  study's only finding; confirming them takes 2 studies off the strip and 472 candidate pairs out,
  leaving 27,297. The owner confirmed both.
- **Frozen.** The normalization prompt, its schema, the outcome rules and the model settings
  were frozen after the re-proposal under the rules, as the extraction prompt was after its
  last gold run. A test pins the request's key, so any change shows; it changes only with the
  owner's approval and a new recording.
- The hosted build refuses an unreviewed mapping. The CLI demo may run unreviewed and labels its
  output so.
- Reruns keep reviewed groups and propose only strings the mapping has not seen, so review effort
  does not repeat.
- **How a rerun adds strings.** The request lists the mapping's outcomes before the new strings,
  so a string can join one; the instructions and schema stay the first proposal's. The result
  is appended as tables of its own, never rewriting the reviewer's text. Tables that share a
  name are one outcome, which is reviewed only when every table of it is, so an outcome that
  gains strings goes back to review for just those strings.
- Hard cases: "depressive symptoms", "PHQ-9 score" and "depression severity" merge, and so do
  "depression" and "depression incidence"; incidence and severity do not. VITAL-DEP reports both
  incidence and PHQ-8 scores, so one trial exercises the rule ([Okereke et al. 2020][vital-dep]).
- **A bare condition is case status.** A string naming only a condition, with no instrument,
  cut-off or status word ("depression", "postpartum depression", "depression (patients versus
  controls)"), is case status; with a rating scale and no cut-off ("depression (BDI)") it stays a
  score. The rule was set by reading the quotes behind the 35 bare-condition strings in the
  vitamin D corpus: of the 34 findings with a clear statistic, 33 report odds, risk, prevalence
  or membership of a depressed group. It is a rule on the string alone, because the model
  normalizes strings without their quotes. One known ambiguity: "depression (survey)" is case
  status by the rule, though its only quote reports paired t-tests on scores.
- The eval scores the model's proposal before review, so it measures the model rather than the
  reviewer.

## Compare and classify

Candidate pairs come from the extracted fields with no model involved; the model only classifies
them, at whatever depth the run asks for.

**Candidates.**

- A pair is two studies on the same normalized outcome whose directions differ: effect vs no
  effect, or benefit vs harm.
- Each study enters a pair through its headline finding on that outcome, so pairs grow with
  studies, not findings. The headline finding is its primary finding on that outcome, else its
  first-reported finding there that is not a subgroup result: the finding the summary strip
  counts, chosen by the same function, so observational studies, which rarely name a primary
  outcome, still pair. A study's other findings show in the finding detail but are not paired
  in v1.
- A study whose only findings on an outcome are subgroup results has no headline finding there.
  It shows in the graph and the node detail but is not counted or paired on that outcome, since
  a subgroup cannot speak for the whole sample. The run manifest counts these cases.
- Pairs within one trial (same PMID or a shared trial ID) and review findings are skipped.
- **One trial, several papers.** A registered study is keyed by the first of its trial IDs in
  sorted order. Its papers are read in PMID order, so its first-reported finding on an outcome
  comes from its earliest paper, and its primary finding still comes first.
- Every run prints and stores the block size per outcome: `run --stage compare` writes the
  pairs, each study's headline finding and the blocks to `compare/candidates.json`. It reads
  the mapping's `out_of_domain` list and counts the findings it drops, and says when the
  mapping is unreviewed.

**Classification.**

- Input: both findings' claim, quote, population, intervention, comparator, measure as reported,
  design, sample size and custom dimensions. Output: a Classification with `genuine` or
  `apparent`, a primary reason from the fixed set, any `also_differs` reasons, a short `detail`
  and a one-sentence rationale; or `off_question`, with no reason, when one finding's exposure is
  not the question's.
- An `off_question` pair is an extraction failure found late: a finding whose exposure is
  another condition, an unrelated gene or a contrast that holds vitamin D fixed. Like a finding
  on an out-of-domain outcome, it is counted, as a drop in the method section, and left out of
  the contradiction panel and the eval. Its findings stay on the graph, since each may still pair
  with a vitamin D finding by another study. A combination product that includes vitamin D, and
  a genetic proxy for vitamin D level in a Mendelian randomization study, are on the question.
- **Extraction finding, measured on the 300 eval pairs:** 89 pairs, 30%, hold one of 8
  headline findings whose exposure is not vitamin D, 5% of the 151 headline findings the
  pairs draw on. They are vitamin C taken or measured, cognitive impairment status, light
  therapy, serum magnesium, ethnicity, a proprietary supplement that never names vitamin D,
  and B12 with folic acid given against a placebo that carried the same vitamin D. A leaked
  finding pairs with every study on its outcome that points the other way, so 5% of findings
  become 30% of pairs. Every arm marks 84 to 88 of the 89 `off_question`. One more finding,
  overweight combined with vitamin D status, is borderline, and the arms split on its two
  pairs. The misreading `off_question` invites, a measured or self-reported vitamin D exposure
  taken for another, appears only in `single-small`, on 5 of the 300 pairs, 1.7%. Extraction
  should drop these findings itself; until it does, classification counts them.
- **Pairs drawn before a polarity override:** 29 of the 300 eval pairs, 12 of them among the 70
  labeled, set the 15D trial's finding, read then as harm, against a benefit. They stay in the
  eval and are classified as drawn, by the outcome's polarity, so their recordings and the label
  set stand; each label calls the pair apparent on the outcome measure, since both sides
  improved. The corpus no longer has these pairs, and no dev pair holds the finding.
- The prompt was tuned on the 40 dev pairs in two rounds and then frozen. In the first, both
  single arms called pairs apparent for differences alone, against the rules, and stumbled on
  findings whose exposure was not vitamin D; the arms agreed on 22 verdicts and on 15 verdicts
  with their reason. The second round added `off_question` and said apparent covers two cases
  only. The arms then agreed on 33 verdicts and 25 with their reason, and both marked the same
  six pairs `off_question`. What is left is for the eval to measure: `single-small` misses the
  underpowered rule on pairs where the abstract itself flags it, and where several differences
  stand out, the arms split between `population` and `design_or_bias`. Both rounds cost $0.14.
- `--depth` picks one arm of the council ablation, and arm names double as depth names.

| Arm | What runs per pair |
| --- | --- |
| `single-small` | Haiku 5.5, one sample |
| `single-mid` | Sonnet 5.5, one sample |
| `self-consistency` | Sonnet 5.5, k samples, majority vote; k set so billed tokens match `council-ranked` |
| `self-moa` | Sonnet 5.5, three samples, as many as the council's members, then Sonnet 5.5 synthesizes them |
| `council` | Sonnet 5.5, GPT-6.1 Sol and Gemini 4 Argon answer; Sonnet 5.5 chairs and synthesizes |
| `council-ranked` | As `council`, with anonymized peer ranking before synthesis, following [llm-council][llm-council] by Andrej Karpathy |

Claude models are the default everywhere outside the council arms. The council's other vendors
and models were chosen after a price check on 2026-11-16, from each vendor's own pricing page:
mid-tier peers priced like Sonnet 5.5, $2 / $10 per million input and output tokens at standard
rates, half that in batches. OpenAI's GPT-6.1 Sol is priced the same. Google's Gemini 4 Argon is
too through 2026-11-30, and doubles on 2026-12-01, so the council's one paid run on the 300 goes
out before then; replays cost nothing. Their SDKs, `openai` and `google-genai`, are pinned
exactly in an optional `council` extra, and only `vendors.py` imports them, during live runs.

- **Reasoning:** every member runs at its vendor's lowest setting. The Claude arms run without
  thinking; GPT-6.1 Sol and Gemini 4 Argon cannot turn reasoning off, and their lowest is
  "low". Their hidden reasoning is billed as output and counted in every cost and token figure.
- **The same question:** each member gets the frozen classification prompt and the same pair, with
  the schema's references written out for the other vendors. Gemini's batches enforce a schema only
  in the OpenAPI form of `response_schema`, and ignore a `response_json_schema`, which the dev run
  showed; they return fields in alphabetical order whatever the ordering asked, which still puts the
  rationale before the reason and the verdict. The chair, the rankers and Self-MoA's synthesizer get
  that prompt plus a short statement of their role, and the answers as anonymous responses in an
  order fixed by the pair. Ranking follows llm-council: each member ranks every answer, and the
  chair sees each answer's mean position.
- **Failures:** each call gets one retry on invalid output. A member invalid twice is left out,
  and the chair synthesizes the rest; a pair whose chair is invalid twice is unclassified.
- **Matching k:** self-consistency's k is council-ranked's billed tokens per pair, every input,
  cache and output token at any rate, hidden reasoning included, over one single-mid sample's,
  rounded. It is measured on council-ranked's run on the 300, before self-consistency runs:
  20,995 billed tokens per pair against single-mid's 3,469, so k is 6.
  Samples differ as repeated calls do, since current models take no sampling parameters; ties
  in the vote go to the earlier sample.

**Dry run.** Every stage that calls a model has a `--dry-run` that prices its unrecorded requests
with the dated `prices.toml` and sends nothing: `run --stage extract`, `run --stage normalize`,
and `classify --arm`, which also scales the dev run's measured cost per pair to the pairs it
counts.

Nothing is sent without a dry run first. Extraction's dry run counts the first requests that have no
recording, at about three characters per token, a low figure on purpose for the current tokenizer,
and gives two numbers at batch prices. The typical cost is one request each, with no cache reads and
590 output tokens, the mean over the 67 requests of the first dev run. The ceiling is every abstract
taking all three requests at full output, which real spend cannot pass. It refuses a request that
could pass 100,000 prompt tokens, where Haiku 5.5's price changes. Each run then prints what its
recorded responses cost from their token counts.

Classification's dry run works the same way, per arm. A multi-call arm's later requests depend
on earlier answers, so it is run against a client that sends nothing and answers each request
with a placeholder of typical length, and every request it would make is priced. It gives three
numbers at batch prices: the typical cost, with 190 visible output tokens per request, the dev
runs' mean, plus 1,000 hidden reasoning tokens on each request to another vendor, an assumption
the live run replaces with its counts; the same with Claude's instructions read from the cache,
as they were on the dev runs; and the ceiling, every request retried at full output. A single
arm's dry run also scales the current dev run's measured cost per pair.

**Panel.** Genuine contradictions are grouped by reason and ordered by the two studies' combined
design weight, then combined sample size. Apparent contradictions sit in a collapsed list under
the panel, each with the classifier's one-line reason, such as "underpowered: n = 40".

## Summary and graph

The summary strip counts studies per normalized outcome; the graph holds the same data for the
page to draw.

- **Unit:** studies, per normalized outcome. A study's direction on an outcome is its headline
  finding's: its primary finding on that outcome, failing that its first-reported finding there
  that is not a subgroup result. A study with only subgroup findings on an outcome is shown but
  not counted there. Papers linked through any shared trial registration count once.
- **Weighted share:** the sum of design weights per direction, divided by the total.
- **Sensitivity line:** recompute with every study weighted 1, and with weight = sample size, a
  stand-in for inverse-variance weighting when abstracts give no effect sizes. The strip says
  when either changes the leading direction.
- **Thin outcomes:** under 5 studies, the strip shows counts only, without the weighted bar or the
  sensitivity line, and says the evidence is thin.
- **Reviews and meta-analyses:** listed, not counted. Narrative reviews sit in a collapsed list with
  their count, under the systematic reviews and meta-analyses.
- **Graph:** a NetworkX graph whose nodes are outcome hubs and findings. Edges link each finding
  to its outcome, plus genuine contradictions with their reason. It is exported as node-link JSON
  with a per-outcome summary block, and a JSON schema test guards the export.
- **Layout** is computed deterministically in the browser, so the export carries data, not
  coordinates. NetworkX holds the graph model and the export; it does no layout or analytics here.
- **The export** is one JSON file per question, and the page reads nothing else. Pydantic
  models in `export.py` define it; `schema/export.schema.json` is generated from them and the
  site's TypeScript types from the schema, and tests fail if either falls behind. It holds the
  question and its dimensions, each outcome's summary block and apparent pairs, the studies, the
  graph in NetworkX's node-link form, the reviews and the method numbers. Every finding carries
  its verbatim quote. Fixture exports for the page are synthetic, with 999-prefixed PMIDs.
- **Shipping the export:** the page quotes a sentence from every finding's abstract, and most
  of those abstracts are under publisher copyright, so the export is never committed. `export
  publish` builds it, uploads it to a public GitHub release with `gh` under a name carrying its
  SHA-256, and writes the name and hash to the committed `site/export.lock.json`. The deploy runs
  `export fetch`, which downloads the pinned asset and refuses it if the hash differs. Each
  commit keeps pointing at the export it was published with. Publishing refuses an unreviewed
  mapping and stub contradictions. The deploy fails when the fetched export is over 1 MB
  gzip, what a phone downloads from Pages: about 5 s on Lighthouse's slow 4G, before which the
  page draws nothing. With stub contradictions it is 6.0 MB raw, 0.43 MB gzip and 0.28 MB
  brotli. Until an export is pinned, the deploy builds from the synthetic fixture, and a manual
  run can ask for that build too. Rejected: committing the export (publisher text in git
  history for good), quotes only from openly licensed articles (about two thirds of findings
  would lose their sentence), and fetching abstracts from PubMed in the reader's browser (a
  runtime dependency on NCBI, for the same quotes).
- **Ties and missing sizes:** when the top shares tie, no direction leads. The sample-size line
  counts only studies that report a sample size and is left out when none does. Designs outside
  the weight table, such as other, are shown with their headline finding but not counted.

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
  card and the pairs. Each rule, its check and the result:
  - *Real buttons and links:* `e2e/a11y.spec.ts` walks the tab order and requires every stop to
    be a native button, link or disclosure, apart from the weighted bar's segments and the
    finding card's scrolling details, which take focus to be read. It also looks for any
    interactive role, click handler or pointer cursor outside a native control in the graph
    view, the list view and the pair page. Passes.
  - *4.5:1 text contrast:* `src/tokens.test.ts` checks each text token on its backgrounds, and
    axe's color-contrast rule runs on every outcome at 390 px, in both views, on the pair page
    and at each stop of the keyboard path. Passes.
  - *Not by colour alone:* `e2e/a11y.spec.ts` checks that every graph dot's name starts with its
    direction and the dot sits in that direction's sector (benefit left of the hub, no effect
    right, harm below), that the sector labels and the legend name each direction, that the
    weighted bar keeps the order benefit, no effect, harm with each segment named, and that every
    list row sits under a heading naming its direction. Passes.
  - *Colour-blindness simulation:* `src/tokens.colour-vision.test.ts`, part of the site check,
    simulates protanopia, deuteranopia and tritanopia with Machado, Oliveira and Fernandes'
    full-severity matrices. Every pair of direction colours stays at least CIEDE2000 10 apart,
    several just-noticeable differences, since the dots are small; each direction colour meets
    WCAG's 3:1 for graphics on the surface and the band; the tab accent stays 10 from benefit
    and harm. Closest pair: benefit and no effect under tritanopia, 16.9. Lowest contrast: harm
    on the band under deuteranopia, 3.12:1. Passes.
  - *Keyboard path:* `e2e/a11y.spec.ts` tabs from the outcome tabs through the weighted bar, the
    graph or list toggle, a list row, the finding card's details (scrolled with Page Down) and
    its PubMed link, to a contradiction pair, opens it and follows its link back; every stop
    shows a focus outline. `e2e/graph.spec.ts` covers the graph's single tab stop and its arrow
    keys. Passes.
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
| Extraction | Hand-labeled abstracts, including the baseline vitamin D dimension | 32 | Design and sample size per abstract, design three ways: review tags first, every design tag first, and the model's answers alone; for findings matched to the labels, precision and recall for the match itself, outcome, effect, primary, subgroup and each labeled dimension; verbatim-quote rate; off-topic abstracts with no findings; how often design falls to Other | Replay |
| Normalization | Each outcome string labeled score, case status, remission, response or out of domain; pair verdicts follow from the kinds | 547 strings, so every pair; 200 drawn pairs, including the hard cases, as a subset | Merge precision and recall over every pair of in-domain strings, and on the drawn pairs with the stratum sizes; out-of-domain strings counted | Replay |
| Contradictions | Drawn candidate pairs, labeled genuine, apparent or off the question, with the primary reason and any also_differs | 300 pairs drawn, both studies openly licensed, stratified by outcome, pair kind and designs; 70 labeled, the arms' disagreements capped at 50 plus 20 agreement pairs | Precision on genuine; reason accuracy, strict and lenient, with a confusion matrix; per stratum and on the 300 as drawn | Replay |
| Council ablation | The contradiction set | The 300 pairs | Accuracy and cost per pair for each arm, with paired-bootstrap 95% intervals | Replay; live runs by hand |
| Cost per run | The run manifests' token counts × `prices.toml`, and the outcome proposal's recordings | Every recorded run | Dollars per run and per stage, `claimgraph costs` | Local runs only; `runs/` is not in CI |

- **Gold sizes:** extraction gold is sized to catch a broken field, such as a design that always
  falls to Other or an effect read the wrong way round, not to rank close prompts. It is 32
  hand-labeled abstracts, and at about 80% accuracy a field scored once per abstract has a 95%
  interval of roughly ±14 points. Labeling effort goes to the contradiction set instead, where
  the ablation compares arms.
- **Outcome-pair draw:** the normalization eval scores the proposal before review, so its pairs
  come from the recorded proposal, not from the mapping the reviewer edits. Every pair of strings
  falls in one stratum, the first it fits: both are hard cases the question names; a scale with
  a cut-off against the same scale without; a cut-off against a diagnosis or incidence; a
  diagnosis or incidence against a score; a general term against a named scale; the proposal
  grouped them; the proposal split them but they share an acronym or a word found in at most a
  tenth of the strings; or neither. The draw takes every named pair, 10 of each other hard kind,
  75 grouped and 75 split-but-related pairs. Split pairs sharing neither are nearly all plain
  non-merges, so they are counted, not drawn. On vitamin D that is 200 of 149,331 pairs from 547
  strings: 61,070 grouped, 1,041 split but related and 52,897 split and unrelated. Because the
  strata come from the proposal, precision and recall on the drawn pairs are not its rates over
  all pairs, and the eval reports them with the stratum sizes. Pairs with an out-of-domain
  string are left out of both and counted.
- **Kinds, not pairs:** the labels began as verdicts on the 200 drawn pairs. Under the guide a
  pair's verdict follows from its two strings: out of domain if either is, merge if both are
  the same kind, separate otherwise. So each string gets one kind instead (score, case status,
  remission, response or out of domain), which labels all 149,331 pairs for the effort of 547
  strings. The eval scores merges over every pair of in-domain strings and reports the drawn
  pairs as a subset, so the hard-case strata still show. Pair-by-pair labeling also proved
  inconsistent: 29 of the 114 first-pass pair verdicts disagreed with the per-string labels,
  mostly splits on scale or timing and merges of a cut-off with a score, and that is why the
  labeling unit changed. Polarity follows the kind too: lower is better for score and case
  status, higher for remission and response, and the mapping review sets it so, with reviewed
  exceptions for instruments scored the other way.
- **Corrections found where a model disagrees:** checking a label only where a proposal
  disagrees with it can raise that proposal's score but never lower it, since a wrong label the
  proposal happens to agree with is never looked at. 20 of the 23 outcome corrections came that
  way: 17 from the first proposal's disagreements and 3 from the rules proposal's. The other 3
  came from comparing the kinds with the first-pass pair verdicts, before scoring.
  `outcome_kind_corrections.toml` records how each was found, and the README reports each
  proposal before and after the corrections its own disagreements turned up.
- **Scoring the proposal:** `claimgraph eval normalization` replays the current prompt's proposal
  from the strings in the kind file, so CI needs no run outputs; `--baseline` replays the recording
  the pairs were drawn from, by its key, so the first proposal stays scorable after the prompt
  changes. The strata are always the draw's. `--readme` writes both proposals into the README, each
  before and after the corrections its own disagreements found, with the frozen prompt's proposal by
  stratum and a note that its rates are in sample. CI prints both. A string the proposal puts under
  an outcome it marks outside the domain merges with nothing, since review deletes that outcome, and
  the eval counts how many strings each side puts out of domain.
- **Contradiction pair draw:** every arm's requests are recorded and replayed in CI, and each
  carries both findings' quotes, so the 300 eval pairs come only from candidates whose two
  headline papers are CC BY or CC0 by both licence sources, the gold rule. The strata are
  outcome, pair kind and the two designs: trial and trial, trial and observational,
  observational and observational, or with another design; case series count as observational.
  The reason cannot be a stratum, because it is the label: it is unknown until the pair is
  labeled, so the draw stratifies only on what the extraction fixes. Benefit against harm is
  about one candidate in twenty, so it gets 100 of the 300. Within each pair kind the slots are
  shared equally across strata, a stratum with another design taking half a share, since its
  pairs carry no design weight and sort last on the panel; a stratum that runs out frees its
  slots for the rest. Because of that oversampling, results are reported per stratum and on
  the 300 as drawn, never as accuracy over all candidate pairs. The 40 dev pairs (13 benefit
  against harm) come from studies no eval pair touches, under any licence, and only their IDs
  are committed. `gold contradiction-pairs sample` draws both with a fixed seed into
  `contradiction_sample.json`.
- **Measured on vitamin D:** 3,124 of the 27,945 candidate pairs at the draw, before the polarity
  override, have both papers openly licensed, and the bucket's metadata agreed with PMC's licence
  filters on every one. The eval limits that follow: no remission or treatment response pair has
  both papers open, so neither outcome is in the eval; depression cases has one trial-against-trial
  pair; and benefit against harm has 17 pairs of a trial against an observational study (16 on
  depressive symptoms, 1 on depression cases). Results in those cells say little.

  | Outcome | Pair kind | Designs | Candidates | Both open | Eval | Dev |
  | --- | --- | --- | ---: | ---: | ---: | ---: |
  | Depression cases | Benefit vs harm | Observational, observational | 650 | 40 | 40 | 3 |
  | Depression cases | Benefit vs harm | Trial, observational | 10 | 1 | 1 | 3 |
  | Depression cases | Benefit vs harm | With other | 150 | 2 | 2 | 2 |
  | Depression cases | Effect vs none | Observational, observational | 4,590 | 369 | 33 | 4 |
  | Depression cases | Effect vs none | Trial, observational | 1,013 | 50 | 33 | 4 |
  | Depression cases | Effect vs none | Trial, trial | 14 | 1 | 1 | 4 |
  | Depression cases | Effect vs none | With other | 1,433 | 328 | 17 | 2 |
  | Depressive symptoms | Benefit vs harm | Observational, observational | 432 | 94 | 41 | 3 |
  | Depressive symptoms | Benefit vs harm | Trial, observational | 102 | 16 | 16 | 2 |
  | Depressive symptoms | Effect vs none | Observational, observational | 9,114 | 980 | 33 | 4 |
  | Depressive symptoms | Effect vs none | Trial, observational | 8,723 | 1,042 | 33 | 4 |
  | Depressive symptoms | Effect vs none | Trial, trial | 1,530 | 144 | 33 | 3 |
  | Depressive symptoms | Effect vs none | With other | 181 | 57 | 17 | 0 |
  | Remission and treatment response | Effect vs none | Any | 3 | 0 | 0 | 2 |
  | All | | | 27,945 | 3,124 | 300 | 40 |
- **Pre-registered contradiction scoring**, written before any arm classified an eval pair:
  - *Correct:* an arm is right on a pair when its verdict and its primary reason both match
    the label. Precision on genuine uses the verdict alone: of the pairs an arm calls genuine,
    the share labeled genuine.
  - *Off the question:* a pair labeled `off_question` is left out of every score and counted,
    so fewer than 300 pairs can be scored. A pair an arm calls `off_question` that the label
    puts on the question counts as wrong, and each arm's `off_question` count is reported
    beside its scores. This rule was added before any arm classified an eval pair.
  - *What is labeled:* every eval pair where any two arms differ on the verdict or the primary
    reason, capped at 50 by a seeded draw proportional to the strata; and 20 pairs where every
    arm agrees, drawn at random with a seed, in an order that later labels can extend.
    `gold contradiction-pairs label-set` replays every arm and writes the set, committed as
    `contradiction_label_set.json`; on the 300, 104 pairs disagree, in 9 strata, and 196
    agree, so 70 are labeled. The labeling page shows only those, mixed in one order, and never
    an arm's answer or which group a pair came from.
  - *Why that is enough for the ablation:* on a pair where all arms agree, every arm is right
    or every arm is wrong, so it adds nothing to any paired difference. The difference between
    two arms is the disagreement pairs' share of the 300 times their mean difference in
    correctness on the labeled disagreement pairs, each weighted by the inverse of its chance
    of being drawn when the cap binds. Its 95% interval is a paired bootstrap over the labeled
    disagreement pairs, resampled within strata, 10,000 times with a fixed seed.
  - *Absolute accuracy and precision on genuine* weight the two groups by their shares of the
    300: the disagreement pairs' labeled rate and the agreement pairs' labeled rate, which is
    the same for every arm. The interval resamples both groups. With 20 agreement labels it is
    wide, roughly ±8 to ±11 points when arms agree on 60 to 80% of pairs; the README states the
    widths, and more agreement pairs can be labeled later in the same order.
  - *Reporting:* per stratum and on the 300 as drawn, never as accuracy over all candidate
    pairs. Reason accuracy comes with a confusion matrix on the labeled pairs, weighted the
    same way, and per-reason rates come with their intervals, not as headlines.
  - *Reason accuracy, strict and lenient:* reported both ways, side by side in the README
    table. Strict: the arm's primary reason is the label's primary reason. Lenient: it is the
    label's primary reason or one of the label's `also_differs`. Where several differences
    stand out, `population` against `design_or_bias` split the two single arms on about 8 of
    the 40 dev pairs, so strict alone would understate every arm on an ambiguity known in
    advance; the labeler fills `also_differs` wherever a second difference is as strong as the
    primary one. Both are over the scored pairs, weighted as above, and an arm that gives no
    reason, by calling a pair `off_question`, is wrong on both. *Correct*, above, stays strict:
    the ablation's paired differences use it, and lenient is reported beside them as a
    secondary figure only. Added before any arm saw the 300.
  - *Scope of the hosted run:* the retrieval scope stays title and abstract. If the paired 95%
    interval for the best arm's gain over `single-small` includes zero, every candidate pair is
    classified at `single-small`. Otherwise, the top N pairs per outcome in the panel's order
    (combined design weight, then combined sample size) are classified at the cheapest arm
    whose paired interval against the best arm includes zero, with N the largest that keeps
    the dry run's estimate within the $5 classification budget; an outcome with fewer than N
    pairs has all of them classified. The best arm is the one with the highest estimated
    accuracy, and cost is measured per pair on the 300. The README states how many of the
    candidate pairs were classified. With at most 50 labeled disagreements, "not significantly
    worse" is a loose test, so the rule is likely to land on a cheap arm.
  - *Post-hoc sensitivity, committed before scoring:* the README and this file also report
    every figure without the 29 eval pairs drawn before the 15D polarity override, labeled as
    a post-hoc check. Those pairs test whether an arm catches a polarity error, read then as
    harm against a benefit, that the corpus no longer has. The primary figure stays as
    pre-registered: strict, on every scored pair. The scope rule is applied to the primary
    figure. If the primary and the sensitivity figure would pick different arms, the build
    stops for the owner's ruling, and the README reports both whichever way it goes.
  - *The ruling on the two figures:* they picked different arms: council for the top pairs on
    the pre-registered figure, single-small for every pair without the 29 pairs. The owner
    ruled that the pre-registered figure picks council only because of the 15D pairs, a data
    error the corpus no longer has, and that without them no arm separates from single-small.
    So the hosted page uses single-small for every pair, unless single-small's precision on
    genuine without those pairs is clearly below council's, their intervals not overlapping,
    and then council for the top pairs per outcome, as the pre-registered figure says. They do
    not overlap: 84.1% (79.0% to 87.6%) against 93.2% (88.7% to 97.3%), so council classifies
    the top pairs. `eval contradictions` applies the ruling and the README reports both figures.
  - *Corrections after scoring:* 5 agreement labels contradicted the written Mendelian
    randomization rule, which predates them, and every arm gave the rule's reason on all 5. The
    owner accepted them, so every arm's reason accuracy rises alike and no paired difference
    moves; the README shows the rates before and after. Agreement labels the arms dispute for
    other reasons stay as labeled, since correcting only where models disagree can only raise
    their scores.
  - *How the scorer weights:* each group of the label set, a disagreement stratum or the
    agreement pairs, stands for all of its pairs, so a labeled pair weighs its group's pairs
    over its group's labeled pairs; for a disagreement pair that is the inverse of its chance of
    being drawn. Every figure is a weighted share over the labeled pairs on the question, so
    the pairs labeled `off_question` drop out of numerator and denominator alike, and a paired
    difference is the difference of two arms' rates, which agreement pairs cannot move. The
    post-hoc check takes its pairs out of their groups' counts too, so its weights stay exact.
    Intervals are percentiles of 10,000 rounds seeded with the label set's seed.
  - *Prompt:* the classification prompt is tuned on the 40 dev pairs only and frozen before
    any arm runs on the 300. It is settled when no error pattern shows on more than 2 dev pairs,
    after at most two rounds, the rule extraction used.
- **Matching findings:** the extraction eval pairs each gold finding with a model finding on
  the quote alone. One sentence can back several findings, so findings that share a sentence
  pair by the most subgroup and outcome words in common. Matching finds the same finding;
  scoring then judges its fields, subgroup and outcome included, so neither one's wording
  decides a match.
- **Why the quote alone.** Matching first used the quote and the subgroup name. The first
  scored gold run showed that subgroup names are free text and often left out of a label: a
  label without a name and a model finding with one, or "white" against "white participants",
  never matched. The rule changed after seeing gold scores, so both numbers are kept: on the
  same outputs, 20 findings matched on quote and subgroup, and 26 on the quote alone. The
  second labeling pass uses the same rule, and its agreement did not move.
- **Known misses:** when two Results sentences report the same result, the quote rule allows
  either, and a label and the model that quote different ones count as a missed finding on
  each side. Two gold records do this, 32945627 and 30157761; they stay as misses rather than
  tighten a rule the labels already follow.
- **Precision and recall:** a finding field is correct on a matched pair when the two agree.
  Precision is the correct count over the model's findings, and recall over the labeled
  findings, so a finding the model adds lowers precision and one it misses lowers recall. Output
  still invalid after the retry scores nothing. A wrong design is listed with its cause: the
  publication type, or the first question where the model's answers leave every path to the
  gold design.
- **Scoring the outcome:** two outcome strings count as the same measure when they share an
  instrument, or, when neither names one, when they have the same content words, ignoring order,
  word endings and filler such as "score" or "scale". An instrument is an abbreviation in either
  string, such as PHQ-2 or CES-D, or a full name the abstract defines beside its abbreviation, found
  by Schwartz and Hearst's algorithm ([Schwartz and Hearst 2003][schwartz-hearst]). Case and hyphens
  are ignored, but versions count: PHQ-2 is not PHQ-9. So "PHQ-2" and "depression risk (PHQ-2)"
  agree, and "HAMD-17" and "anhedonia (RSAS)" do not. The second labeling pass scores outcomes the
  same way.
- **Replay cache:** responses are keyed by a hash of model, parameters and request. CI fails on a
  missing response rather than calling out. Requests send no sampling parameters: the current
  API rejects `temperature`, `top_p` and `top_k` on every model, so a run cannot be pinned to
  temperature 0. The recorded response is what makes a rerun reproducible, and because a fresh
  live run can differ, the extraction eval reports run-to-run agreement beside its scores.
- **Recordings:** each file holds the request beside its response and is named by the request's
  hash, which is checked when it is read. The instructions and response schema every abstract
  shares are stored once per prompt version, in `shared/` under their own hash, and each
  recording names them; a run that re-records removes the files no current request uses. Gold
  recordings are committed in `replay/`; dev recordings stay in the run directory with the dev
  abstracts.
- **Leakage guard:** gold abstracts never appear as prompt examples, and the normalization prompt
  never sees labeled strings or pairs. Every extraction prompt change is motivated by something
  seen in the dev set, never by a gold record: a gold error is answered in the prompt only when
  dev shows the same pattern. Normalization has no dev set: its outcome rules were written while
  labeling the same strings the eval scores, and some name instruments seen only there, so a
  proposal under those rules is scored in sample, and the README says so beside its numbers.
- **The ablation's question:** does multi-model deliberation beat the best single model at the
  same token budget? Prior work suggests ensembling, not deliberation, drives most of the gain.
  Either result goes in the README, and the table doubles as the guide to `--depth`.
- **Results come from gold:** the README's results table is generated from the gold sample, never
  from dev.
- **Related work:** Evidence Inference 2.0 ([DeYoung et al. 2020][evinf]) is human-labeled, with
  MIT-licensed annotations, and covers the effect-and-quote task; it is a candidate external
  benchmark. Contradiction detection over biomedical claims is studied by
  [Sosa et al. 2023][sosa], [Alamri and Stevenson 2016][alamri] and [BioDivergence][biodivergence].

### Gold and dev samples

`claimgraph gold sample QUESTION` draws two disjoint samples of 50 from the retrieved abstracts,
by the same quotas: 18 trials, 18 observational studies, 3 systematic reviews or meta-analyses,
3 other reviews and 8 off-topic records. Off-topic records count inside the 50, so the
extraction eval also checks that the outcome domain returns no findings for them.

- **Gold** is drawn only from articles under CC BY or CC0, and its abstract text is committed
  with a generated attribution file. Licences come from two sources that must agree: PMC's
  licence search filters, queried through E-utilities, and the `license_code` in each article
  version's metadata in the PMC Cloud Service bucket. A retracted version, any version under
  another licence, or no metadata at all rules an article out. NCBI retired the PMC OA Web
  Service API and the legacy FTP files in August 2026 ([NCBI][pmc-transition], [PMC][pmc-aws]);
  the bucket's README documents the metadata fields ([PMC][pmc-bucket]).
- **Gold is what was labeled.** `claimgraph gold trim QUESTION` keeps only the gold records
  with a passing label and regenerates `gold.jsonl` and the attribution file from them. It
  refuses while a label is in progress, deletes only untouched templates, and leaves the draw's
  counts in `sample.json` beside the labeled ones. For vitamin D, 32 of the 50 were labeled,
  in the labeling page's order within each stratum: 15 sampled as trials, 9 observational, 2
  systematic reviews, 2 other reviews and 4 off topic.
- **Dev** is drawn from the whole kept corpus less the gold sample, under any licence. Only its
  PMIDs, strata and the seed are committed; the text is refetched by PMID into the gitignored
  run directory, so dev is reproducible without redistributing it. Dev is for tuning prompts
  and measuring tokens. CI never replays it, and a test fails if a committed replay file names a
  PMID outside the gold set.
- **Strata** come from PubMed's publication types: trials, systematic reviews or meta-analyses,
  other reviews, and everything else as observational, since most observational studies carry
  no design type. Case reports, excluded by default, and trial protocols are not sampled. The
  hand labels record each study's actual design.
- **Screening.** The walk visits records in a seeded order and takes each one that fits a quota
  with room. Each record it reaches is first read and marked on or off topic in
  `screening.toml`, with a one-line reason: on topic means the abstract reports, or for a review
  summarizes, a result linking vitamin D to an outcome in the domain, null results included.
  Screening only sorts records into quotas; the hand labels are authoritative.
- **Trials are the scarce stratum.** Of 43 trial-tagged records under CC BY or CC0, 21 are on
  topic, and the gold draw takes 18. That is why dev draws from the whole corpus. Of the 15
  labeled, 11 are trials by their abstracts; the other 4 are observational studies tagged as
  trials, among them a secondary analysis of a trial and two studies with trial registrations.
- **Eval limitation: open-access selection.** Gold comes from the 538 of 1,901 kept records
  that are CC BY or CC0. Their design mix matches the corpus, but they skew recent: none
  predates 2010, against 14% of the corpus, and 73% are from 2020 on, against 48%. Extraction
  scores may not carry over to older abstracts. `sample.json` records both compositions.

| Records | Trials | Observational | Systematic reviews | Other reviews | Not sampled | Before 2010 | 2010–2019 | 2020 on |
|---|---|---|---|---|---|---|---|---|
| Kept corpus, 1,901 | 9% | 63% | 6% | 17% | 5% | 14% | 38% | 48% |
| CC BY or CC0, 538 | 8% | 66% | 7% | 16% | 4% | 0% | 27% | 73% |

### Labels

Labels are hand-written TOML, checked by `claimgraph gold check QUESTION`;
[the labeling guide](evals/gold/LABELING.md) gives the rules and worked examples.

- **Abstracts:** one file per gold abstract, `labels/PMID.toml`, with the design, the sample
  size, an optional summary for reviews, and one block per finding in the outcome domain: the
  verbatim quote, the outcome as reported, the effect, whether it is the primary outcome,
  whether it is a subgroup result, and a value for every config dimension.
  `claimgraph gold template` writes a file per abstract with the abstract shown as comments and
  every value blank.
- **Unscored fields are not labeled.** The extraction eval does not score a finding's claim,
  population, intervention or comparator against gold, nor a review's summary; it judges the
  claim only by whether its quote supports it. Gold labels leave the four finding fields out and
  make the summary optional, so labeling time goes to the fields that are scored.
- **Labeling page.** `claimgraph gold review` serves a page on 127.0.0.1 that writes the same
  label files and runs the same check on every edit, so the files stay the source of truth and
  can still be edited by hand. It cuts labeling time without deciding anything: a clicked
  sentence becomes a quote, verbatim by construction; selected words become the outcome; closed
  fields are buttons and keys; topic terms, measures and design cues are highlighted; and the
  guide's rule for the field in focus sits beside the abstract. It fills in no value, so the
  labels stay independent of any model. The page, its flags and its timings live in the
  gitignored `local-data/`. Requests must name the local host and send JSON, so another site
  open in the browser cannot write labels through it.
- **Design by questions.** A labeler does not pick a design by name. Eleven yes-or-no questions
  about the abstract, asked in order and stopping at the first answer that names a design,
  settle it: people or not, own data or a summary, systematic methods, pooling, individual
  patients, whether the researchers gave the exposure, a concurrent comparison group, allocation
  by chance, a proxy or outside exposure, selection by outcome, and timing. The label keeps the
  answers, the check requires the design to follow from them, and so every gold design traces
  back to what the abstract says. The labeling page can also jump straight to a design; the jump
  fills in the answers on that design's path, so the label still records them. The questions are
  worded for any question, with "the
  exposure" and "the outcome"; the labeling guide spells them out for vitamin D and depression.
- **Every value is explicit.** TOML has no null, so a missing value is written
  `"not_reported"`, and a blank template fails the check rather than passing as unlabeled.
  The check also requires each quote to appear in its abstract, after the same whitespace and
  quote-mark normalization the verbatim check uses.
- **Effect points from more vitamin D.** The exposure side of every finding is more vitamin D:
  a higher level, intake or dose, or supplementation against placebo. A result stated as
  deficiency against sufficiency is turned round, so that direction can be derived the same
  way for trials and observational studies. An association reported with depression as the
  exposure, such as lower vitamin D in depressed patients, is read the same way round, with the
  depression measure as the outcome. In extraction, code does the turning round, from the two facts
  the model reports.
- **Primary means named.** A finding is primary only when the abstract calls it the primary or
  a co-primary outcome; a subgroup result on the primary outcome is not.
- **Subgroups are marked.** A subgroup result covers part of the analyzed sample, split by sex,
  age, baseline vitamin D status or similar. A study that enrolled only one group reports that
  group as its population, not as a subgroup.
- **One sentence, several findings.** A sentence often reports two subgroups, or incidence and
  recurrence, so findings may share a quote. A finding is identified by its quote, outcome and
  subgroup name together, and a subgroup result is named whenever another finding shares its
  quote and outcome.
- **Consistency check.** Five labeled records with findings, drawn at random with a fixed seed,
  are labeled a second time, blind. `claimgraph gold relabel` writes blank templates to
  `relabel/` and keeps a copy of the first pass as it stood in `relabel/first/`, so later gold
  corrections don't move the agreement. The labeling page labels them without showing the first
  pass, and `claimgraph gold agreement` counts agreement field by field. Findings pair as the
  extraction eval pairs them, on the quote, so a reworded outcome counts against the outcome
  field rather than as a missed finding. The README reports the agreement beside the
  extraction results.
- **Genuine or apparent is about the results.** A genuine pair's results really point different
  ways, whether or not a reason stands out, and a difference between the studies is that reason,
  never grounds for calling the pair apparent. An apparent pair does not conflict on a close
  read, in two cases only: the null study could not rule out the effect, by its own account or
  its confidence interval, or the two findings measure different things. An apparent pair
  always names the reason that dissolves the conflict. A third verdict, `off_question`, marks a
  pair where one finding's exposure is not vitamin D; it takes no reason.
- **Outcome kinds** label what each reported string measures; whether two strings merge
  follows from their kinds, including in the ruled hard cases.
  **Contradiction pairs** label a candidate pair's verdict, reason, other differences and
  detail, under the same rules as the classifier's output.

### Gold runs

Each live run on the gold set, with what changed since the one before. The README table shows
the latest, generated by `claimgraph eval extraction`; earlier numbers are kept here.

1. **Haiku 5.5, path-aware design answers** (2026-11-13). 22 of 32 responses valid on the first
   try and 1 still invalid after the retry; every error a design-path error. Not scored; its
   recordings were replaced by the next run's.
2. **Haiku 5.5, design answers on facts** (2026-11-13). Changed: every design question
   answered on its facts, with code walking the tree. 32 of 32 valid on the first try. Design 27
   of 32, also 27 from the answers alone; sample size 27 of 32. Findings matched on quote and
   subgroup: 20, precision 20 of 51 and recall 20 of 34; on the quote alone, 26, precision 26
   of 51 and recall 26 of 34. Dev showed findings outside the outcome domain and the exposure's
   level given as the outcome, 20 of its 73 findings, which the next run answers.
3. **Haiku 5.5, domain outcome per finding** (2026-11-13). Changed: each finding names its
   outcome-domain entry or "outside the domain", and the prompt says the exposure's level is
   never the outcome; matching on the quote alone. 32 of 32 valid on the first try; 5 findings
   dropped as outside the domain. Design 26 of 32, also 26 from the answers alone; sample size
   28 of 32. Findings matched: 26, precision 26 of 43 and recall 26 of 34. Off-topic abstracts
   with no findings: 4 of 4. Dev review: one pattern on more than 2 abstracts, a change within
   one group reported as a finding beside a comparison group (3 abstracts), which the next run
   answers.
4. **Haiku 5.5, between groups only and a deficiency example** (2026-11-13). Changed: with a
   comparison group, a change within one group is not a finding; the prompt works through a
   result stated from deficiency, which dev read the wrong way round on 2 abstracts. 32 of 32
   valid on the first try; 9 findings dropped as outside the domain. Design 26 of 32, also 26
   from the answers alone; sample size 28 of 32. Findings matched: 24, precision 24 of 40 and
   recall 24 of 34; effect right on 21 of the 24. Off-topic abstracts with no findings: 4 of 4.
   Dev review: a change within one group still reported beside a comparison group (3
   abstracts), and a result stated from deficiency still read the wrong way round (2
   abstracts).
5. **Haiku 5.5, effect and comparison as facts** (2026-11-13). Changed: each finding reports the
   side its sentence is worded from and the outcome's direction on it, and code works out the
   effect; each finding says what it compares, and code drops within-group and pooled changes
   in trials. The last round of prompt changes: the prompt is frozen after this run. 32 of 32
   valid on the first try; 8 findings dropped as outside the domain and 4 as within one group or
   pooled in a trial. Design 26 of 32, 27 from the answers alone; sample size 28 of 32. Findings
   matched: 26, precision 26 of 38 and recall 26 of 34; effect right on 25 of the 26. Off-topic
   abstracts with no findings: 4 of 4. Two of the dropped trial findings are labeled: one
   follows from a design miss (40226697, a case series extracted as a non-randomized trial), and
   one was a labeled change within one arm (38931184). That label contradicted the
   between-groups rule, so it was corrected after this run and now has no findings, which
   makes recall 26 of 33. Dev review: no direction errors and no
   within-group findings left in trials; the remaining patterns are listed under Failure modes.
   With review tags first, adopted after run 7, its design is 27 of 32.
6. **Sonnet 5.5, the frozen prompt** (2026-11-13). Nothing changed: the same prompt, schema and
   effort as run 5, so the comparison measures the model. 32 of 32 valid on the first try; no
   findings dropped as outside the domain and 3 as within one group or pooled in a trial.
   Design 31 of 32 with review tags first, 30 with every design tag first, 31 from the answers
   alone; sample size 27 of 32. Findings matched: 25, precision 25 of 33 and recall 25 of 33;
   effect right on 24 of the 25. Off-topic abstracts with no findings: 4 of 4. $0.1341 at batch
   prices, against $0.0082 for run 5.
7. **Haiku 5.5, a second run of run 5's requests** (2026-11-13), for run-to-run agreement.
   Design 27 of 32 with review tags first, 26 with every design tag first, 27 from the answers
   alone; sample size 27 of 32. Findings matched: 22, precision 22 of 39 and recall 22 of 33;
   effect right on 21 of the 22. Against run 5: design agrees on 32 of 32 and sample size on 31
   of 32; 33 findings match between the runs, out of 38 and 39, and effect agrees on 31 of
   those 33. $0.0084 at batch prices.
8. **Sonnet 5.5, a second run of run 6's requests** (2026-11-13), so the README's run-to-run
   agreement describes the model in use. Design 31 of 32 with review tags first; sample size 27
   of 32. Findings matched: 26, precision 26 of 32 and recall 26 of 33; effect right on 25 of
   the 26. Against run 6: design agrees on 32 of 32 and sample size on 31 of 32; 30 findings
   match between the runs, out of 33 and 32, and effect agrees on all 30. $0.1305 at batch
   prices.

**Extraction model: Sonnet 5.5.** The choice rests on design, which sets each study's weight.
Sonnet 5.5 gets 31 of 32 designs right against 27 of 32 for Haiku 5.5. Every disagreement
favors Sonnet: it is right on the four records Haiku misses in both of its runs (27336042,
41977424, 40226697 and 33372187), and their one miss is shared (36771476). Four discordant
records all one way give p = 0.125 on an exact sign test, so this is a judgment, not a
significant difference. Haiku's misses repeat across its two runs, which agree on design for
32 of 32 records, so they are systematic, not noise. Findings did not separate the models:
Sonnet's 25 and 26 matched findings over its two runs sit inside Haiku's run-to-run spread of
22 to 26. Extracting the 1,901 kept abstracts on Sonnet cost $5.26 at batch prices; the gold
rate on Haiku puts the same corpus at about $0.50. Haiku stays recorded on gold as the
comparison.

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
$1/$5, Opus 5.5 $2/$10 ([pricing][pricing]). Before measurement, the estimates were extraction of
about 500 abstracts at roughly $0.08 on Haiku or $1.60 on Sonnet, and classification at roughly
$0.07 or $1.30 per 1,000 pairs. Measured spend per run and per stage is in the README's cost
table, generated by `claimgraph costs`, and each arm's measured cost per pair is in its
contradiction table.
Measured on gold, extraction costs about $0.0042 an abstract on Sonnet 5.5 and $0.00026 on
Haiku 5.5. The corpus costs less per abstract than gold does: extracting the 1,901 kept
abstracts on Sonnet 5.5 cost $5.26, or $0.0028 an abstract, because gold oversamples trials and
most corpus abstracts report no finding on depression, so their output is shorter. Among the
1,901, two records carry the same title and abstract, so they make one request between them;
neither reports a finding. The run sent 1,900 first requests, then 2 retries for missed quotes
and none for invalid output. Cache reads were 13.6M of the 14.9M input tokens.

## CLI and repository

A small Typer CLI drives every stage, and each pipeline step is one module.

- `claimgraph run QUESTION [--stage STAGE] [--depth ARM [--alike N [--per-study M]]]
  [--dry-run] [--live]`. QUESTION is a config path or id; a config, not free text, keeps runs
  reproducible. `--stage` runs one stage, `--dev` extracts the dev sample, and `--model` picks
  the extraction model. `--depth` classifies the candidate pairs after compare at one arm;
  `--alike` only the pairs whose studies are alike in N ways or more, and `--per-study` puts no
  study in more than M of them; `--stage classify --depth ARM` runs that step alone.
- `claimgraph mapping check QUESTION` fails when the mapping is unreviewed or a string is
  unmapped. A member no finding reports does not fail it: the name is kept, since a later run
  may report it again. It counts the findings the `out_of_domain` list drops.
- `claimgraph gold sample QUESTION` draws the gold and dev samples; it stops and lists the
  records that need screening first.
- `claimgraph gold outcome-pairs sample QUESTION` draws the outcome pairs from the recorded
  proposal and never redraws over an existing draw. `template` and `check` write and validate
  a file of verdicts on those pairs, the labels the kinds replaced.
- `claimgraph gold outcome-kinds template QUESTION` writes the kind file, every string of the
  recorded proposal with no kind; `check` validates it against the draw and fails until every
  string has a kind; `compare` lists the pair verdicts entered by hand that the kinds do not
  give; `review` serves a page on 127.0.0.1 for labeling the strings by kind, beside the
  guide's outcome section, and saves each click to the file. It reads only the kind file and
  the guide, so the model's grouping cannot show.
- `claimgraph gold template QUESTION` writes a label file to fill in for each gold abstract, and
  `claimgraph gold check QUESTION` validates the labels.
- `claimgraph eval [extraction | normalization | contradictions | models]`, and `claimgraph costs
  QUESTION` for measured spend
- `claimgraph export build QUESTION` writes the page data; `export stub` stands in for
  classification while the page is built.

```
claimgraph/
  pyproject.toml  uv.lock  README.md  DESIGN.md  LICENSE
  src/claimgraph/
    models.py  dimensions.py  config.py  retrieve.py  gold.py  labels.py  extract.py
    verbatim.py  batch.py  evaluation.py  normalize.py  outcome_pairs.py  outcome_kinds.py
    kind_review.py  normalization_eval.py  compare.py  contradictions.py  pair_sample.py
    arms.py  summary.py  graph.py  export.py  export_build.py  paths.py  llm.py  prices.py  cli.py
    classify.py  vendors.py  contradiction_eval.py  contradiction_review.py  label_set.py
    relabel.py  review.py  design_tree.py  measures.py  publish.py  costs.py
  schema/      export.schema.json, generated from export.py
  prices.toml  dated Claude prices, read by the dry run and the cost report
  questions/   vitamin-d-depression.toml  creatine-cognition.toml
  mappings/    vitamin-d-depression.toml
  evals/       gold/LABELING.md  gold/QUESTION/ (gold.jsonl, dev.jsonl, screening.toml,
               sample.json, ATTRIBUTION.md, labels/, outcome_pair_sample.json,
               outcome_kinds.toml, outcome_kind_corrections.toml, contradiction_sample.json,
               contradiction_label_set.json, contradiction_pairs.toml,
               contradiction_corrections.toml, CONTRADICTION_ATTRIBUTION.md, relabel/)
               gold/README.md
  replay/      recorded model responses used by CI
  site/        React + TypeScript front end: src/types/export.ts generated from the schema,
               src/fixtures/ synthetic exports, components/ shell, graph and contradictions
  tests/
  .github/workflows/   ci.yml  pages.yml
```

Tooling: uv, ruff, pyright (strict on `src/`), pytest, Pydantic v2, Typer, httpx for E-utilities,
the Anthropic SDK, and the OpenAI and Google SDKs in the optional `council` extra, on Python 3.12.

## Scaling limits and failure modes

The first limit at 10× the corpus is classification spend on mid-tier arms; the first failure
that reaches readers is mis-normalization.

### Scaling limits

Measured where a number is given; the rest are expectations.

- **Pairs** grow with the product of direction counts within an outcome. At 10× abstracts the
  small arm still fits the budget and the larger arms may not, and the dry run says so before
  spending.
- **Normalization** is one call. At about 24 output tokens a string, its 16,000-token limit
  holds about 650 strings, not a few thousand: past that it needs chunked proposals and a merge
  pass. A rerun proposes only strings the mapping has not seen, so the limit binds on a new
  question, not on a growing one.
- **The graph** stops reading as a picture once a hub holds several hundred findings. The list
  view carries those outcomes.
- **Review effort** scales with new outcome strings, not with runs, because the mapping is
  incremental.
- **Custom dimensions** each add extraction load and error; the cap of five keeps the schema
  small.
- **E-utilities** is not the limit: 10 requests per second fetches tens of thousands of abstracts
  an hour.

### Failure modes

- **Spin:** abstracts can overstate secondary or subgroup results. Counting primary findings,
  never subgroup results, and showing every quote limits the damage.
- **Uncontrolled findings lean toward benefit:** depression scores fall in the placebo arms of
  antidepressant trials too, by a large part of the drug arms' improvement
  ([Rief et al. 2009][rief]), so a single-arm study can show improvement that vitamin D did not
  cause, and historical controls overstate benefit ([Goodacre 2015][goodacre]). Both are case
  series, and the 0.5 weight keeps them light.
- **Absence of evidence read as evidence of absence:** small trials report "no significant
  difference." Sample size shows on every finding, the size-aware line tests the weighting, and
  the classifier can mark such pairs apparent as underpowered.
- **A finding with no stated direction gets a guessed one:** an abstract can report that
  vitamin D was "significantly associated" with depression, or list it among variables related
  to depression risk, without saying which way. Extraction must still give an effect, and
  guesses one. A scan of every finding that reads as harm on a lower-is-better outcome found 2
  such findings among the 1,901 abstracts, 39051261-1 and 40596345-1, both recorded as
  increased, so both read as harm. They go to the reviewed exclusion list as
  `direction_not_reported`. The scan looked only where a guess reads as harm, so a guess that
  reads as benefit would not have shown. Extraction should report such a finding as having no
  stated direction instead; until it does, the review excludes them. 44 of the 300 eval pairs
  hold one of the two, 16 of them labeled, and 5 of the 40 dev pairs; they stay as drawn.
- **Mis-normalized outcomes:** over-merging, under-merging, or a polarity error that flips benefit
  and harm. The reviewed mapping and the normalization eval guard this.
- **Design misclassification:** wrong weights. Design is scored in the extraction eval three
  ways from the same outputs: review tags first, where only Systematic Review, Meta-Analysis
  and Scoping Review tags set the design and the answers set every other; every design tag
  first, the trial tag included; and the model's design answers alone. Review tags are
  reliable: 1 of the 109 records tagged systematic review or meta-analysis lacked a stated
  method. The trial tag is not, since it also lands on secondary analyses that are
  observational. The rule, fixed before the deciding runs: review tags first is adopted if it
  matches or beats every design tag first on both the second Haiku 5.5 gold run and the Sonnet
  5.5 gold run. It did, 27 against 26 on Haiku and 31 against 30 on Sonnet, so extraction
  sets the design that way. Clinical Trial, its phases and Controlled Clinical Trial never set
  a design.
- **Secondary analyses tagged as trials:** a secondary analysis of trial participants that does
  not compare the randomized arms is observational, but PubMed can tag it Randomized Controlled
  Trial. Setting the design from that tag gave such a study trial weight; one of the 15 gold
  records whose types name a design is such a case, labeled cohort. Since the trial tag no
  longer sets the design, the design answers decide it.
- **Extraction errors left at the freeze.** The extraction prompt was frozen after the fifth
  gold run, its changes all motivated by dev. The last dev review, over 50 abstracts, left:
  - the exposure's level named as the outcome in results grouped by the outcome, such as lower
    vitamin D in depressed patients (4 abstracts). The direction is right in three of them; the
    fourth is a light-therapy trial in which vitamin D was the outcome, not the exposure, so
    the finding should not exist. The outcome-mapping review shows these, since the name is no
    depression measure;
  - two contrasts in one sentence, such as deficient against insufficient and against
    sufficient, given as two findings without subgroup names to tell them apart (2);
  - combined or neighbouring measures kept, such as "depression or anxiety" or a well-being
    index (2);
  - an exposure contrast filed as a subgroup, and a significant deficiency result in a sentence
    listing many factors read as no_change (1 each).

  In gold, Haiku 5.5's frozen-prompt run left six designs wrong: two cross-sectional studies sent to
  other by the genetic-proxy question, a narrative review answered as not on people, a
  cross-sectional study read as a cohort, a case series read as a non-randomized trial, and the
  secondary analysis tagged as a trial. Sonnet 5.5, the extraction model, misses one, 36771476,
  which Haiku misses too.
- **Normalization errors left at the freeze.** The normalization prompt was frozen after the
  re-proposal under the outcome rules. It leaves two known failures:
  - one "mixed or non-depression outcome" takes in strings the labels keep in the domain, such
    as "depressed mood" and "depressive symptoms (Brief Symptom Inventory-18)";
  - seasonal affective disorder is still split off, into a case-status and a score outcome of
    its own, because the prompt points the model at the domain list and seasonal affective
    disorder is an entry of it.

  The page shows the reviewed mapping, not the proposal, so neither reaches it; the eval scores
  them.
- **Double counting:** one trial can publish several papers, and large trials publish many
  secondary analyses. The trial-ID merge handles it.
- **A real quote that doesn't support the claim:** claim wording is unscored, so the eval does
  not measure support. The page shows the verbatim quote beside every claim, so a reader checks
  the two against each other.
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

Block sizes, token use, cost and eval scores land here as they are measured.

**Vitamin D candidate pairs**, from the reviewed mapping, after the 15D polarity override and the
23 excluded findings: 24,770 pairs among 425 studies with a headline finding, 24,504 of them an
effect against no effect and 266 benefit against harm. Before the 21 findings whose exposure is
off the question were excluded there were 27,297 pairs, 854 of them benefit against harm: a
finding on another exposure points the other way from many vitamin D findings, so the leaks made
two in three of the benefit-against-harm pairs. Four trials merge 15 papers. Reviews and the
excluded designs (case reports, animal and in vitro studies) never enter a pair. 48 findings are
dropped as out of domain, and 57 study-outcomes have only subgroup results.

| Outcome | Studies | Pairs | Only subgroup results |
| --- | ---: | ---: | ---: |
| Depressive symptoms | 276 | 17,888 | 36 |
| Depression cases | 184 | 6,881 | 21 |
| Remission | 2 | 1 | 0 |
| Treatment response | 2 | 0 | 0 |

An early cost estimate assumed about 3,000 same-outcome pairs for about 500 abstracts. Pairs
grow roughly with the square of the studies on an outcome, and this corpus of 1,901 abstracts is
nearly four times that size, so most of the gap comes from corpus size. Scaled from the dev runs'
measured cost per pair, classifying every pair would cost about $2.38 on `single-small`. The scope
rule, with the owner's ruling, sends the top 93 pairs of each outcome to `council` instead, 93
being the most whose dry-run estimate, $4.98 for the 189 pairs then, fits the $5 classification
budget. The exclusions after that run shifted the top pairs: 26 new ones were classified, for
187 in all, and classification on the corpus cost $1.75 measured: $1.53 for the first 189 and
$0.22 for the 26. All 187 are genuine.

**The most alike pairs.** Ranking by combined design weight put three trials in 153 of the 187
slots: the panel was mostly one large trial against a small observational study, a disagreement
design and size explain before anyone reads it, and every pair came back genuine. So the panel
now shows the pairs whose studies are most alike, where a disagreement cannot be explained away
by design or size. Two studies are alike in a field when both report it and the values match:
the same design (not `other`), the same outcome measure by the extraction eval's same-measure
rule, the same value of each custom dimension (not `not_reported`), and sample sizes within 3x
of each other. Two unreported values are unknown, not alike. For vitamin D that is four fields,
and the page classifies every pair alike in at least three: 245 of 24,770. Four studies were in
more than 10 of them, up to 16, so no study is in more than 8, each keeping its most alike
pairs; that leaves 221 (168 on depressive symptoms, 53 on depression cases). The 187 pairs
classified under the old rule keep their recordings, and the 2 the new rule selects replay
without cost; the rest leave the page, so it shows one selection rule. The accuracy the
README reports is still the stratified 300's, drawn to stand for every candidate pair.
Council on the 219 new pairs cost $1.74. Of the 221, 176 are genuine, 29 apparent and 16 off
the question, against none apparent under the old rule; population is the reason for 141 of
the 205 on the question, the outcome measure for 28, design or bias for 16, the intervention for
11, the comparator for 7, and 2 are unclear. Population leading is expected once design,
measure, baseline status and size are held close, since it is most of what is left to differ.
But it is also council's weakest call on the stratified 300: of the 15 labeled pairs it gave
population, the label's primary reason is population in 4, a weighted precision of 44% (6% to
73%), and population is the primary or an also-differs reason in 8, 59% (27% to 86%). Its
misses were mostly pairs labeled design or bias, or comparator. So the 141 says the population
differs in most of these pairs, not that it is the main reason in each.

Two papers on the same trial or dataset match on every field, so the selection was checked for
them by string matching, without a model. Papers sharing a trial registration are already one
study. No classified pair includes a review, and no pair names the same trial; 3 of the 221
pair Zhao 2010 with a later paper on the same survey, NHANES, from different cycles and so
different people (Huang 2025, Guo 2025, Chen 2025).

**Vitamin D retrieval** (records added to PubMed through October 31, 2026; searched November 5,
2026). The topic terms and MeSH headings are the config's; both filters, non-research
publication types and animal-only studies, apply to every row.

| Scope | Found | Kept | Dropped |
|---|---|---|---|
| Title and abstract (`title_abstract`) | 1,947 | 1,901 | 31 no abstract, 11 books, 4 retracted |
| Title (`title`) | 470 | 456 | 13 no abstract, 1 retracted |

- **Filters.** Without them, the earlier hand-written query found 2,145 records. Dropping
  non-research types removes 51, and dropping animal-only studies removes a further 217. Writing
  the animal filter as `humans[mh]` instead would also drop about 390 records not yet indexed
  with MeSH, most of them recent.
- **Terms.** A phrase search for "vitamin D" matches inside "25-hydroxy vitamin D" but not
  "vitamin D3", so the variants are listed. Each candidate was measured against the rest of the
  list and kept only if what it added was on topic, judged by title. `25(OH)D` adds 8 records.
  "25-hydroxyvitamin D3" adds 2, and 3 in title scope, among them a study of depression
  severity. "seasonal affective disorder" adds 5, 3 of them on seasonal depression. "25OHD" and
  "calcidiol" add none, and "25(OH)D3" adds 4, all bone or kidney studies, so those three are not
  listed.
- **Mood.** A bare `mood` term added 142 records; of 10 sampled from what it and `25(OH)D` added, 1
  was on topic, 1 marginal and 8 off topic, such as quality-of-life or pain studies that mention
  mood in passing, so it was dropped. In its place, "depressed mood" adds nothing that `depress*`
  does not already find, and "low mood", "mood disorder" and "mood disorders" add 3, 2 and 22
  records with none on the outcome domain: a record they add never uses a word starting with
  "depress". None is listed.
- **MeSH headings.** `Vitamin D`, `Depression` and `Mood Disorders` add 142 records to
  title-and-abstract scope. Of 26 distinct records sampled from what each heading adds, read by
  title and abstract, 1 was on topic (a review of add-on treatments for depression), 3 marginal
  (premenstrual mood symptoms, psychological distress, parathyroid hormone and depression) and
  22 off topic, mostly bone and mineral studies. `Mood Disorders` also brings in bipolar disorder
  studies, which the outcome domain leaves off the map. Restricting the headings to major topics
  (`[majr]`) keeps 52 of the 142, but it loses the on-topic review and one of the marginal
  records, and it drops no larger share of the bone and mineral studies: by title, 29 of those
  are dropped and 29 kept. The headings stay at `[mh]`.
- **Raw cache.** About 19 KB per record. The cache keeps records from earlier term lists, and the
  vitamin D run directory is about 50 MB.
- **What title scope costs.** Under the earlier term list, title-and-abstract scope kept 1,439
  records that title scope did not; with the current list it keeps 1,445. In a seeded random
  sample of 20 of the 1,439, read in full, 10 measured depression as a primary
  or secondary outcome, and 8 reported a finding linking vitamin D to depression: 5 observational
  or uncontrolled studies and 3 reviews. Three more reported only mood, and 7 were off topic. One
  of the 20 was missed for want of a term, not because of scope: its title reads
  "25-hydroxyvitamin D3", now a listed term. On this sample, title scope would drop
  roughly two in five broad-only records that bear on the question, most of them studies that
  report depression secondarily. The sample is small; the interval on 8 of 20 is wide.
- **Review publication types.** PubMed's types do not separate systematic from narrative
  reviews reliably. Of 326 kept records tagged Review but not Systematic Review, Meta-Analysis
  or Scoping Review, at least 21 are systematic reviews, meta-analyses or scoping reviews by
  their own title or stated methods, and about 18 more describe a database search without
  saying how studies were chosen. Seven systematic reviews or meta-analyses carry no type but
  Journal Article, as do 716 kept records in all, most of them recent. Of 109 records tagged
  Systematic Review or Meta-Analysis, 1 describes no method in its abstract. By type and
  wording, about 300 kept records, 16%, are narrative reviews; extraction gives the count.
- **Which scope the demo uses:** title and abstract, as the scope rule under Evals fixes.

## References

- [NLM: E-utilities rate limits][nlm-limits]
- [NCBI: E-utilities usage guidelines][ncbi-guidelines]
- [NLM: copyright and downloading data][nlm-copyright]
- [NLM: MEDLINE citations by language][nlm-lang]
- [PubMed DTD: OtherAbstract][dtd-other]
- [NLM: MEDLINE DataBank sources and trial registries][nlm-databank]
- [NCBI Insights: PMC article dataset distribution changes in 2026][pmc-transition]
- [PMC: accessing the article datasets on AWS][pmc-aws]
- [PMC Cloud Service bucket README][pmc-bucket]
- [Claude API pricing][pricing]
- [Cochrane Handbook 5.1, section 9.5.1: what is heterogeneity][cochrane]
- [Cochrane Handbook 6.5.1, section 4.4.4: controlled vocabulary and text words][cochrane-search]
- [GRADE: indirectness and PICO][grade-pico]
- [Cochrane EPOC: what study designs should be included in an EPOC review][epoc-designs]
- [CEBM glossary: case series][cebm-glossary]
- [Rief et al. 2009, placebo response in antidepressant trials][rief]
- [Sintonen 2001, the 15D instrument][sintonen]
- [Goodacre 2015, uncontrolled before-after studies][goodacre]
- [Schwartz and Hearst 2003, finding abbreviation definitions in biomedical text][schwartz-hearst]
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
[pmc-transition]: https://ncbiinsights.ncbi.nlm.nih.gov/2026/02/12/pmc-article-dataset-distribution-services/
[pmc-aws]: https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/
[pmc-bucket]: https://pmc-oa-opendata.s3.amazonaws.com/README.txt
[pricing]: https://platform.claude.com/docs/en/about-claude/pricing
[cochrane]: https://training-noproxy.cochrane.org/handbook/archive/v5.1/chapter_9/9_5_1_what_is_heterogeneity.htm
[cochrane-search]: https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-04
[grade-pico]: https://scholarworks.aub.edu.lb/items/3ff7d4a6-32f2-46d7-9ffe-15425f8d58fd
[epoc-designs]: https://epoc.cochrane.org/sites/epoc.cochrane.org/files/public/uploads/Resources-for-authors2017/what_study_designs_should_be_included_in_an_epoc_review.pdf
[cebm-glossary]: https://www.cebm.ox.ac.uk/resources/ebm-tools/glossary
[rief]: https://pubmed.ncbi.nlm.nih.gov/19246102/
[sintonen]: https://pubmed.ncbi.nlm.nih.gov/11491191/
[goodacre]: https://emj.bmj.com/content/32/7/507
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
[schwartz-hearst]: https://pubmed.ncbi.nlm.nih.gov/12603049/
