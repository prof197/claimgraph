"""Builds one question's export from a run: the summary strips, the graph and the method.

Everything comes from the run's files and the committed gold data and recordings, and every list
is ordered by fixed keys, so the same inputs always give the same export.
"""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from claimgraph import (
    compare,
    contradiction_eval,
    evaluation,
    graph,
    normalization_eval,
    outcome_kinds,
    pair_sample,
)
from claimgraph.batch import ExtractManifest
from claimgraph.config import QuestionConfig
from claimgraph.export import (
    SCHEMA_VERSION,
    ApparentPair,
    ContradictionEdge,
    Dimension,
    Drop,
    EvalResult,
    Export,
    FindingNode,
    Method,
    Outcome,
    Question,
    Review,
    StageCost,
    Study,
)
from claimgraph.extract import DEFAULT_MODEL, ExtractedStudy, ExtractionResult
from claimgraph.gold import read_abstracts
from claimgraph.labels import load_abstract_label
from claimgraph.models import (
    DESIGN_WEIGHTS,
    REVIEW_DESIGNS,
    Arm,
    ClassifiedPairs,
    Design,
    Finding,
    Verdict,
)
from claimgraph.normalization_eval import NormalizationScores
from claimgraph.normalize import OutcomeMapping
from claimgraph.outcome_pairs import SAMPLE_FILE, load_sample
from claimgraph.retrieve import Abstract, DropReason, RetrieveManifest, RunManifest
from claimgraph.summary import HeadlineStudy, summarize

# Where the committed recordings the evals replay live, as the eval commands default to.
REPLAY_ROOT = Path("replay")

_RECORD_DROPS = {
    DropReason.BOOK: "book records",
    DropReason.RETRACTED: "retracted records",
    DropReason.NO_ABSTRACT: "records without an abstract",
    DropReason.NON_ENGLISH_ONLY: "records with no English abstract",
}
# Finding fields as the method section names them; dimensions take their label.
_FIELD_NAMES = {
    "finding": "finding",
    "outcome_reported": "outcome",
    "effect": "effect",
    "is_primary": "primary",
    "is_subgroup": "subgroup",
}

Paper = tuple[Abstract, ExtractedStudy]


def dimension_label(name: str) -> str:
    """A dimension's name as words: one-letter words are names, as in vitamin D."""
    text = " ".join(w.upper() if len(w) == 1 else w for w in name.split("_"))
    return text[0].upper() + text[1:]


def _lowered(label: str) -> str:
    return label[0].lower() + label[1:]


def evaluated_dimensions(gold_dir: Path) -> set[str]:
    """The dimensions every labeled gold finding has a value for; none without gold labels."""
    labels = [load_abstract_label(p) for p in sorted((gold_dir / "labels").glob("*.toml"))]
    covered = [set(f.dimensions) for label in labels for f in label.findings]
    return covered[0].intersection(*covered[1:]) if covered else set()


def extraction_evals(scores: evaluation.ExtractionScores) -> list[EvalResult]:
    rows = [
        ("design", scores.design.agree, scores.design.of),
        ("sample size", scores.sample_size.agree, scores.sample_size.of),
    ]
    for field in scores.findings:
        name = _FIELD_NAMES.get(field.name) or _lowered(dimension_label(field.name))
        rows += [
            (f"{name} precision", field.correct, field.model),
            (f"{name} recall", field.correct, field.gold),
        ]
    verbatim = scores.quotes_checked - scores.quote_misses
    rows.append(("quotes verbatim on the first output", verbatim, scores.quotes_checked))
    return [
        EvalResult(eval="extraction", metric=metric, value=agree / of, n=of, low=None, high=None)
        for metric, agree, of in rows
        if of
    ]


def normalization_evals(scores: NormalizationScores) -> list[EvalResult]:
    merges = scores.all_pairs
    rows = [("merge precision", merges.proposed), ("merge recall", merges.labeled)]
    return [
        EvalResult(
            eval="normalization", metric=metric, value=merges.both / of, n=of, low=None, high=None
        )
        for metric, of in rows
        if of
    ]


def classification_evals(scores: contradiction_eval.Scores, depth: Arm) -> list[EvalResult]:
    """The hosted arm's verdict and strict reason accuracy on the labeled eval pairs."""
    arm = next((a for a in scores.arms if a.arm is depth), None)
    if arm is None:
        return []
    return [
        EvalResult(
            eval="classification",
            metric=metric,
            value=figure.estimate,
            n=scores.scored,
            low=figure.low,
            high=figure.high,
        )
        for metric, figure in (("verdict", arm.verdict), ("reason", arm.reason))
        if figure.estimate is not None
    ]


def _evals(
    config: QuestionConfig, gold_dir: Path, replay_root: Path, depth: Arm | None
) -> list[EvalResult]:
    """The extraction and normalization evals, replayed, for whichever gold data exists, and
    the hosted arm's classification figures as the contradiction eval recorded them."""
    rows: list[EvalResult] = []
    if (gold_dir / "gold.jsonl").exists():
        scores = evaluation.gold_scores(gold_dir.parent, replay_root, config, DEFAULT_MODEL)
        rows += extraction_evals(scores)
    if (gold_dir / outcome_kinds.LABEL_FILE).exists():
        labels = normalization_eval.labeled(gold_dir)
        drawn = load_sample(gold_dir / SAMPLE_FILE)
        replay = replay_root / config.id / "normalization" / DEFAULT_MODEL.value
        proposal = normalization_eval.replayed(labels, config, replay, DEFAULT_MODEL, drawn, False)
        assert proposal.outcomes is not None
        found = normalization_eval.score(labels, proposal.outcomes, proposal.outside, drawn)
        rows += normalization_evals(found)
    scores_path = gold_dir / contradiction_eval.SCORES_FILE
    if depth is not None and scores_path.exists():
        scores = contradiction_eval.Scores.model_validate_json(scores_path.read_text())
        rows += classification_evals(scores, depth)
    return rows


def _drops(
    retrieved: RetrieveManifest,
    extracted: ExtractManifest,
    found: compare.Candidates,
    excluded: int,
    off_question: int,
    config: QuestionConfig,
) -> list[Drop]:
    """What each stage left out, in pipeline order."""
    designs = ", ".join(d.value.replace("_", " ") for d in Design if d in config.exclude_designs)
    rows = [
        ("records PubMed did not return", len(retrieved.unavailable)),
        *((what, retrieved.dropped.get(reason, 0)) for reason, what in _RECORD_DROPS.items()),
        ("abstracts without a recorded extraction", extracted.missing),
        ("abstracts whose extraction stayed invalid", extracted.invalid),
        ("findings the model marked outside the outcome domain", extracted.outside_domain),
        ("trial findings within one group or pooled across groups", extracted.within_or_pooled),
        ("findings whose quote was not verbatim", extracted.quotes_dropped),
        ("findings returned for a review", extracted.review_findings_dropped),
        ("findings on outcomes the mapping review left out of the domain", found.out_of_domain),
        *(
            (f"findings the review excluded: {reason.value.replace('_', ' ')}", count)
            for reason, count in found.excluded_findings.items()
        ),
    ]
    if designs:
        rows.append((f"findings from designs the question excludes: {designs}", excluded))
    # An extraction failure that classification finds: the finding should have been dropped.
    rows.append(
        ("candidate pairs where one finding's exposure is not the question's", off_question)
    )
    return [Drop(what=what, count=count) for what, count in rows]


def _study(key: str, papers: list[Paper]) -> Study:
    """One study from its papers in PMID order: the earliest leads and gives the design."""
    abstract, lead = papers[0]
    sizes = [s.sample_size for _, s in papers if s.sample_size is not None]
    return Study(
        key=key,
        pmids=[a.pmid for a, _ in papers],
        title=abstract.title,
        first_author=abstract.first_author,
        journal=abstract.journal,
        year=abstract.year,
        design=lead.design,
        weight=DESIGN_WEIGHTS.get(lead.design),
        sample_size=sizes[0] if sizes else None,
        trial_ids=sorted({t for a, _ in papers for t in a.trial_ids}),
        trial=lead.design in pair_sample.TRIALS,
    )


def _review(abstract: Abstract, study: ExtractedStudy) -> Review:
    return Review(
        pmid=abstract.pmid,
        title=abstract.title,
        first_author=abstract.first_author,
        journal=abstract.journal,
        year=abstract.year,
        design=study.design,
        summary=study.review_summary,
    )


def _read_results(path: Path) -> list[ExtractionResult]:
    return [ExtractionResult.model_validate_json(line) for line in path.read_text().splitlines()]


def _check_classified(found: compare.Candidates, classified: ClassifiedPairs) -> None:
    known = {p.pair_id for p in found.pairs}
    unknown = [c.pair_id for c in classified.classifications if c.pair_id not in known]
    if classified.candidates != len(found.pairs) or unknown:
        raise ValueError(
            f"the classified pairs are not from this comparison ({classified.candidates} "
            f"candidates and {len(unknown)} unknown pairs against {len(found.pairs)}); "
            "classify the current candidates again"
        )


@dataclass(frozen=True)
class _Corpus:
    """The run's papers as the export shows them: primary studies with findings, and reviews."""

    # Each study's findings on each mapped outcome, by (outcome, study key), in reported order.
    findings: dict[tuple[str, str], list[Finding]]
    studies: dict[str, Study]
    reviews: list[Review]
    # Findings from designs the question excludes, which are neither shown nor counted.
    excluded: int


def _corpus(
    results: list[ExtractionResult],
    abstracts: dict[str, Abstract],
    mapping: OutcomeMapping,
    config: QuestionConfig,
) -> _Corpus:
    """Papers in PMID order, so a trial's findings and its lead come from its earliest paper."""
    keys = compare.study_keys({p: a.trial_ids for p, a in abstracts.items()})
    outcome_of = {m: o.name for o in mapping.outcomes for m in o.members}
    excluded_findings = mapping.excluded()
    findings: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    papers: dict[str, list[Paper]] = defaultdict(list)
    reviews: list[Review] = []
    excluded = 0
    for result in sorted(results, key=lambda r: int(r.pmid)):
        study = result.study
        if study is None:
            continue
        abstract = abstracts[result.pmid]
        if study.design in config.exclude_designs:
            excluded += len(study.findings)
        elif study.design in REVIEW_DESIGNS:
            reviews.append(_review(abstract, study))
        else:
            key = keys.get(result.pmid, result.pmid)
            # Strings out of the domain have no outcome, and the comparison counts their findings
            # and the ones the review excluded.
            mapped = [
                f
                for f in study.findings
                if f.outcome_reported in outcome_of and f.finding_id not in excluded_findings
            ]
            for finding in mapped:
                findings[(outcome_of[finding.outcome_reported], key)].append(finding)
            if mapped:
                papers[key].append((abstract, study))
    return _Corpus(
        findings=dict(sorted(findings.items())),
        studies={key: _study(key, papers[key]) for key in sorted(papers)},
        reviews=sorted(reviews, key=lambda r: (-r.year, int(r.pmid))),
        excluded=excluded,
    )


def _tabs(corpus: _Corpus, mapping: OutcomeMapping) -> list[str]:
    """From the outcome the most studies report on, the mapping's order breaking ties."""
    reporting = Counter(outcome for outcome, _ in corpus.findings)
    names = [o.name for o in mapping.outcomes if o.name in reporting]
    return sorted(names, key=lambda name: -reporting[name])


def _finding_nodes(
    corpus: _Corpus,
    tabs: list[str],
    headline: dict[tuple[str, str], str],
    mapping: OutcomeMapping,
    config: QuestionConfig,
) -> list[FindingNode]:
    """Tab by tab and study by study, so a pair's study a comes before its study b."""
    polarity = mapping.polarity()
    return [
        FindingNode(
            kind="finding",
            id=finding.finding_id,
            outcome=outcome,
            study=key,
            pmid=finding.pmid,
            direction=compare.direction(finding.effect, polarity[finding.outcome_reported]),
            headline=headline.get((outcome, key)) == finding.finding_id,
            claim=finding.claim,
            quote=finding.quote,
            outcome_reported=finding.outcome_reported,
            is_primary=finding.is_primary,
            subgroup=finding.subgroup,
            population=finding.population,
            intervention=finding.intervention,
            comparator=finding.comparator,
            dimensions={d.name: finding.dimensions.get(d.name) for d in config.dimensions},
        )
        for outcome in tabs
        for (on, key), findings in corpus.findings.items()
        if on == outcome
        for finding in findings
    ]


@dataclass(frozen=True)
class _Classified:
    genuine: list[ContradictionEdge]
    apparent: dict[str, list[ApparentPair]]
    # Classified pairs by outcome, off-question pairs included.
    counts: Counter[str]
    # Pairs left out because one finding's exposure is not the question's.
    off_question: int


def _classified(
    found: compare.Candidates,
    classified: ClassifiedPairs,
    studies: dict[str, Study],
    headline: dict[tuple[str, str], str],
    results: list[ExtractionResult],
    dimensions: list[str],
) -> _Classified:
    """The classified pairs in the comparison's order, each between two headline findings."""
    verdicts = {c.pair_id: c for c in classified.classifications}
    findings = {f.finding_id: f for r in results if r.study is not None for f in r.study.findings}
    designs = {r.pmid: r.study.design for r in results if r.study is not None}
    every_field = pair_sample.FIXED_FIELDS + len(dimensions)

    def closely_matched(a: Study, b: Study, finding_a: str, finding_b: str) -> bool:
        fa, fb = findings[finding_a], findings[finding_b]
        shared = pair_sample.shared_fields(
            fa, fb, (designs[fa.pmid], designs[fb.pmid]), (a.sample_size, b.sample_size), dimensions
        )
        return shared == every_field

    genuine: list[ContradictionEdge] = []
    apparent: dict[str, list[ApparentPair]] = defaultdict(list)
    counts: Counter[str] = Counter()
    off_question = 0
    for pair in found.pairs:
        verdict = verdicts.get(pair.pair_id)
        if verdict is None:
            continue
        counts[pair.outcome] += 1
        if verdict.reason is None:
            # Only an off_question pair has no reason; it compares nothing on the question.
            off_question += 1
            continue
        a, b = studies[pair.study_a], studies[pair.study_b]
        finding_a, finding_b = headline[(pair.outcome, a.key)], headline[(pair.outcome, b.key)]
        if verdict.verdict is Verdict.APPARENT:
            apparent[pair.outcome].append(
                ApparentPair(
                    pair_id=pair.pair_id,
                    pair_kind=pair.kind,
                    study_a=a.key,
                    study_b=b.key,
                    finding_a=finding_a,
                    finding_b=finding_b,
                    reason=verdict.reason,
                    detail=verdict.detail,
                    closely_matched=closely_matched(a, b, finding_a, finding_b),
                )
            )
            continue
        size_a, size_b = a.sample_size, b.sample_size
        genuine.append(
            ContradictionEdge(
                kind="contradiction",
                source=finding_a,
                target=finding_b,
                pair_id=pair.pair_id,
                pair_kind=pair.kind,
                reason=verdict.reason,
                also_differs=verdict.also_differs,
                detail=verdict.detail,
                rationale=verdict.rationale,
                # A design without a weight adds nothing to the panel's order.
                design_weight=(a.weight or 0) + (b.weight or 0),
                sample_size=None if size_a is None or size_b is None else size_a + size_b,
                closely_matched=closely_matched(a, b, finding_a, finding_b),
            )
        )
    return _Classified(
        genuine=genuine, apparent=dict(apparent), counts=counts, off_question=off_question
    )


def _outcome(
    name: str,
    found: compare.Candidates,
    studies: dict[str, Study],
    pairs: _Classified,
    mapping: OutcomeMapping,
) -> Outcome:
    block = next(b for b in found.blocks if b.outcome == name)
    leads = [
        HeadlineStudy(
            study=h.study,
            design=studies[h.study].design,
            sample_size=studies[h.study].sample_size,
            direction=h.direction,
        )
        for h in found.headlines
        if h.outcome == name
    ]
    return Outcome(
        name=name,
        hub=graph.hub_id(name),
        higher_is_better=next(o.higher_is_better for o in mapping.outcomes if o.name == name),
        summary=summarize(leads, block.subgroup_only),
        candidate_pairs=block.pairs,
        classified_pairs=pairs.counts[name],
        apparent=pairs.apparent.get(name, []),
    )


def _question(
    config: QuestionConfig,
    retrieved: RetrieveManifest,
    abstracts: int,
    mapping: OutcomeMapping,
    gold_dir: Path,
) -> Question:
    evaluated = evaluated_dimensions(gold_dir)
    return Question(
        id=config.id,
        title=config.title,
        retrieved=retrieved.searched_at.date(),
        abstracts=abstracts,
        mapping_reviewed=mapping.reviewed,
        dimensions=[
            Dimension(
                name=d.name,
                label=dimension_label(d.name),
                short_label=d.short_label,
                refines=d.refines,
                unit=d.unit,
                evaluated=d.name in evaluated,
            )
            for d in config.dimensions
        ],
        found=config.found,
    )


def study_facts(
    results: list[ExtractionResult],
    abstracts: dict[str, Abstract],
    mapping: OutcomeMapping,
    config: QuestionConfig,
) -> dict[str, tuple[float | None, int | None]]:
    """Each study's design weight and sample size, by study key, as the page shows them."""
    corpus = _corpus(results, abstracts, mapping, config)
    return {key: (s.weight, s.sample_size) for key, s in corpus.studies.items()}


def build_export(
    config: QuestionConfig,
    run_dir: Path,
    mapping: OutcomeMapping,
    classified: ClassifiedPairs,
    gold_dir: Path,
    replay_root: Path = REPLAY_ROOT,
) -> Export:
    """The export for `config` from the run in `run_dir`, with its contradictions from
    `classified`. A dimension counts as evaluated when the gold labels in `gold_dir` cover it.
    The same inputs always give the same export, byte for byte."""
    retrieved = RunManifest.model_validate_json((run_dir / "manifest.json").read_text()).retrieve
    entry = f"corpus-{DEFAULT_MODEL.value}"
    manifests = json.loads((run_dir / "extract" / "manifest.json").read_text())
    extracted = ExtractManifest.model_validate(manifests["runs"][entry])
    results = _read_results(run_dir / "extract" / f"{entry}.jsonl")
    abstracts = {a.pmid: a for a in read_abstracts(run_dir / "abstracts.jsonl")}
    trial_ids = {p: a.trial_ids for p, a in abstracts.items()}
    found = compare.candidates(results, trial_ids, mapping, config.exclude_designs)
    _check_classified(found, classified)
    corpus = _corpus(results, abstracts, mapping, config)
    tabs = _tabs(corpus, mapping)
    headline = {(h.outcome, h.study): h.finding_id for h in found.headlines}
    nodes = _finding_nodes(corpus, tabs, headline, mapping, config)
    dimensions = [d.name for d in config.dimensions]
    pairs = _classified(found, classified, corpus.studies, headline, results, dimensions)
    return Export(
        schema_version=SCHEMA_VERSION,
        question=_question(config, retrieved, len(results), mapping, gold_dir),
        outcomes=[_outcome(name, found, corpus.studies, pairs, mapping) for name in tabs],
        studies=list(corpus.studies.values()),
        graph=graph.node_link(graph.build(tabs, nodes, pairs.genuine)),
        reviews=corpus.reviews,
        method=Method(
            records_found=retrieved.found,
            abstracts_kept=retrieved.kept,
            findings=len(nodes),
            studies=len(corpus.studies),
            drops=_drops(retrieved, extracted, found, corpus.excluded, pairs.off_question, config),
            candidate_pairs=len(found.pairs),
            classified_pairs=len(classified.classifications),
            depth=classified.depth,
            selection=classified.selection,
            costs=[StageCost(stage="extract", dollars=extracted.cost_usd)],
            evals=_evals(config, gold_dir, replay_root, classified.depth),
        ),
    )
