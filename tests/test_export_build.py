"""The export built from a small synthetic run: every record, PMID and trial ID is invented.

The run has seven primary papers on synthetic symptoms, two of them one trial, a case report
the question excludes, and two reviews. Each expected number is worked in the comments.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from claimgraph import export_build
from claimgraph.batch import ExtractManifest
from claimgraph.compare import Direction
from claimgraph.config import Dimension, DimensionType, QuestionConfig
from claimgraph.evaluation import Count, ExtractionScores, FieldScore
from claimgraph.export import (
    ContradictionEdge,
    Export,
    FindingNode,
    HubNode,
    OutcomeEdge,
    Weighting,
)
from claimgraph.extract import DesignSource, ExtractedStudy, ExtractionResult
from claimgraph.models import (
    Arm,
    ClassifiedPairs,
    Comparison,
    Design,
    Effect,
    ExposureSide,
    Finding,
    NormalizedOutcome,
    PairClassification,
    Reason,
    Verdict,
)
from claimgraph.normalization_eval import MergeCounts, NormalizationScores
from claimgraph.normalize import OutcomeMapping
from claimgraph.retrieve import (
    Abstract,
    AbstractSection,
    DropReason,
    RetrieveManifest,
    RunManifest,
    TopicMatch,
)

SYMPTOMS, REMISSION = "Synthetic symptoms", "Synthetic remission"
CONFIG = QuestionConfig(
    id="synthetic-question",
    title="Does synthetic supplement X affect synthetic mood?",
    exposure_terms=["synthetic supplement X"],
    outcome_terms=["synthetic mood"],
    outcome_domain=["synthetic symptoms", "synthetic remission"],
    end_date=date(2026, 1, 31),
    dimensions=[
        Dimension(
            name="synthetic_dose",
            definition="The synthetic daily dose.",
            type=DimensionType.NUMBER,
            refines=Reason.INTERVENTION,
            unit="mg",
        )
    ],
)
MAPPING = OutcomeMapping(
    outcomes=[
        NormalizedOutcome(
            name=SYMPTOMS,
            members=["synthetic mood score", "synthetic low mood"],
            higher_is_better=False,
            reviewed=True,
        ),
        NormalizedOutcome(
            name=REMISSION, members=["synthetic remission"], higher_is_better=True, reviewed=True
        ),
    ],
    out_of_domain=["synthetic distress"],
)
TRIAL = "SYNTHETIC:T1"
DOWN, UP, SAME = Effect.DECREASED, Effect.INCREASED, Effect.NO_CHANGE
# (outcome string, effect, primary, subgroup name)
Spec = tuple[str, Effect, bool, str | None]
# pmid: (design, sample size, trial IDs, findings)
PAPERS: dict[str, tuple[Design, int | None, list[str], list[Spec]]] = {
    "999000101": (
        Design.RCT,
        120,
        [],
        [("synthetic mood score", DOWN, True, None), ("synthetic remission", UP, False, None)],
    ),
    "999000102": (Design.RCT, 18000, [TRIAL], [("synthetic mood score", SAME, True, None)]),
    # A later paper of the same trial: its finding shows but the earlier primary leads.
    "999000112": (Design.RCT, 500, [TRIAL], [("synthetic low mood", UP, False, None)]),
    "999000103": (
        Design.COHORT,
        5600,
        [],
        [("synthetic low mood", DOWN, False, None), ("synthetic distress", UP, False, None)],
    ),
    "999000104": (Design.RCT, None, [], [("synthetic mood score", DOWN, False, "women")]),
    "999000105": (Design.CROSS_SECTIONAL, 640, [], [("synthetic low mood", UP, False, None)]),
    "999000106": (Design.OTHER, None, [], [("synthetic mood score", SAME, False, None)]),
    "999000107": (Design.CASE_REPORT, 1, [], [("synthetic mood score", DOWN, False, None)]),
    "999000108": (Design.RCT, 60, [], [("synthetic mood score", DOWN, True, None)]),
}
# pmid: (design, year, summary)
REVIEWS = {
    "999000201": (Design.META_ANALYSIS, 2019, "Synthetic pooled benefit on symptoms."),
    "999000202": (Design.NARRATIVE_REVIEW, 2024, None),
}


def pair_id(a: str, b: str) -> str:
    return f"{SYMPTOMS}|{a}|{b}"


# Symptoms headlines, in key order: 101 benefit, 103 benefit, 105 harm, 106 none, 108 benefit
# and the trial none. Pairs whose directions differ: 101, 103 and 108 against 105, 106 and the
# trial make 9, and 105 against 106 and the trial 2 more, so 11. Remission has one study.
CANDIDATES = 11
CLASSIFIED = [
    PairClassification(
        pair_id=pair_id("999000101", TRIAL),
        verdict=Verdict.GENUINE,
        reason=Reason.POPULATION,
        also_differs=[Reason.INTERVENTION],
        detail="synthetic: depressed adults vs a general population",
        rationale="Synthetic rationale: the populations differ.",
    ),
    PairClassification(
        pair_id=pair_id("999000105", "999000108"),
        verdict=Verdict.GENUINE,
        reason=Reason.DESIGN_OR_BIAS,
        also_differs=[],
        detail="synthetic: a survey vs a trial",
        rationale="Synthetic rationale: the designs differ.",
    ),
    PairClassification(
        pair_id=pair_id("999000103", "999000106"),
        verdict=Verdict.APPARENT,
        reason=Reason.OUTCOME_MEASURE,
        also_differs=[],
        detail="synthetic: a mood item vs a symptom scale",
        rationale="Synthetic rationale: the measures differ.",
    ),
]


def abstract(pmid: str, year: int, trial_ids: list[str]) -> Abstract:
    return Abstract(
        pmid=pmid,
        pmcid=None,
        title=f"Synthetic study {pmid[-3:]} of supplement X and mood",
        first_author=f"Synthetic{pmid[-3:]}",
        journal="Synthetic Journal",
        year=year,
        language="eng",
        publication_types=[],
        mesh=[],
        trial_ids=trial_ids,
        sections=[AbstractSection(label=None, category=None, text=f"Synthetic abstract {pmid}.")],
    )


def finding(pmid: str, n: int, spec: Spec) -> Finding:
    outcome, effect, primary, subgroup = spec
    return Finding(
        finding_id=f"{pmid}-{n}",
        claim=f"Synthetic finding {n} of synthetic study {pmid[-3:]}.",
        quote=f"Synthetic sentence {n} from synthetic abstract {pmid}.",
        outcome_reported=outcome,
        effect=effect,
        is_primary=primary,
        is_subgroup=subgroup is not None,
        subgroup=subgroup,
        population="Synthetic adults",
        intervention="Synthetic supplement X, 50 mg daily",
        comparator="Synthetic placebo",
        exposure_side=ExposureSide.MORE,
        outcome_direction=effect,
        comparison=Comparison.BETWEEN,
        domain_outcome="synthetic symptoms",
        dimensions={"synthetic_dose": 50.0},
    )


def result(
    pmid: str, design: Design, size: int | None, findings: list[Finding]
) -> ExtractionResult:
    summary = REVIEWS[pmid][2] if pmid in REVIEWS else None
    study = ExtractedStudy(
        pmid=pmid,
        design=design,
        design_from=DesignSource.ANSWERS,
        design_answers={},
        sample_size=size,
        review_summary=summary,
        findings=findings,
        other_outcomes=[],
        primary_subgroups=0,
        review_findings_dropped=0,
        outside_domain=0,
        within_or_pooled=0,
    )
    return ExtractionResult(
        pmid=pmid,
        model="synthetic",
        study=study,
        errors=[],
        quotes_checked=len(findings),
        quote_misses=[],
        quotes_dropped=0,
        usage=[],
    )


def manifests() -> tuple[RunManifest, ExtractManifest]:
    retrieved = RetrieveManifest(
        question=CONFIG.id,
        scope=TopicMatch.TITLE_ABSTRACT,
        term="synthetic term",
        scope_counts={TopicMatch.TITLE: 5, TopicMatch.TITLE_ABSTRACT: 14},
        date_range="records added to PubMed through January 31, 2026",
        searched_at=datetime(2026, 1, 15, 2, 30, tzinfo=UTC),
        found=14,
        fetched=14,
        cached=0,
        unavailable=[],
        kept=11,
        dropped={DropReason.NO_ABSTRACT: 2, DropReason.BOOK: 1},
        languages_kept={"eng": 11},
        languages_dropped={},
        requests=2,
        raw_bytes=1000,
    )
    extracted = ExtractManifest(
        model="synthetic",
        abstracts=11,
        extracted=11,
        missing=0,
        invalid=0,
        findings=11,
        outside_domain=2,
        within_or_pooled=1,
        other_outcomes=3,
        primary_subgroups=0,
        review_findings_dropped=0,
        single_arm=0,
        quotes_checked=11,
        quote_misses=1,
        quotes_dropped=1,
        requests=12,
        input_tokens=100,
        cache_write_tokens=0,
        cache_read_tokens=0,
        output_tokens=50,
        cost_usd=0.25,
        prices_checked=date(2026, 1, 1),
    )
    return RunManifest(retrieve=retrieved), extracted


def write_run(run_dir: Path, reverse: bool = False) -> None:
    """The run's files as the pipeline writes them; reversed, the lines come in the other order."""
    abstracts = [abstract(p, 2010 + int(p[-2:]), t) for p, (_, _, t, _) in PAPERS.items()]
    abstracts += [abstract(p, year, []) for p, (_, year, _) in REVIEWS.items()]
    results = [
        result(p, design, size, [finding(p, n, s) for n, s in enumerate(specs, start=1)])
        for p, (design, size, _, specs) in PAPERS.items()
    ]
    results += [result(p, design, None, []) for p, (design, _, _) in REVIEWS.items()]
    if reverse:
        abstracts.reverse()
        results.reverse()
    retrieved, extracted = manifests()
    (run_dir / "extract").mkdir(parents=True)
    (run_dir / "manifest.json").write_text(retrieved.model_dump_json(indent=2))
    (run_dir / "abstracts.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in abstracts))
    (run_dir / "extract" / "corpus-sonnet.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in results)
    )
    entry = {"runs": {"corpus-sonnet": json.loads(extracted.model_dump_json())}}
    (run_dir / "extract" / "manifest.json").write_text(json.dumps(entry))


CLASSIFIED_PAIRS = ClassifiedPairs(
    depth=Arm.SINGLE_SMALL, candidates=CANDIDATES, classifications=CLASSIFIED
)


def build(tmp_path: Path, reverse: bool = False) -> Export:
    run_dir = tmp_path / ("reversed" if reverse else "run") / CONFIG.id
    write_run(run_dir, reverse)
    return export_build.build_export(
        CONFIG, run_dir, MAPPING, CLASSIFIED_PAIRS, tmp_path / "gold" / CONFIG.id
    )


def findings_of(export: Export) -> list[FindingNode]:
    return [n for n in export.graph.nodes if isinstance(n, FindingNode)]


def test_the_export_is_valid_and_round_trips(tmp_path: Path) -> None:
    export = build(tmp_path)

    assert Export.model_validate_json(export.model_dump_json()) == export


def test_the_same_run_gives_the_same_bytes_whatever_order_its_lines_come_in(
    tmp_path: Path,
) -> None:
    assert build(tmp_path).model_dump_json() == build(tmp_path, reverse=True).model_dump_json()


def test_the_question_states_the_search_day_and_unevaluated_dimensions(tmp_path: Path) -> None:
    question = build(tmp_path).question

    assert question.retrieved == date(2026, 1, 15)
    assert question.abstracts == 11
    assert question.mapping_reviewed
    assert [(d.name, d.label, d.unit, d.evaluated) for d in question.dimensions] == [
        ("synthetic_dose", "Synthetic dose", "mg", False)
    ]


def test_the_symptoms_strip_works_out_by_hand(tmp_path: Path) -> None:
    # Counted: 101 benefit (rct, 120), the trial none (rct, 18,000), 103 benefit (cohort,
    # 5,600), 105 harm (cross-sectional, 640) and 108 benefit (rct, 60). 106 is other:
    # uncounted. 104 has only a subgroup result. Design: benefit 3 + 2 + 3 = 8, none 3, harm 1,
    # of 12. Sample size: benefit 5,780, none 18,000, harm 640, of 24,420: no effect leads.
    export = build(tmp_path)
    symptoms, remission = export.outcomes

    assert (symptoms.name, remission.name) == (SYMPTOMS, REMISSION)
    summary = symptoms.summary
    assert (summary.studies, summary.uncounted, summary.subgroup_only) == (5, 1, 1)
    assert (summary.counts.benefit, summary.counts.none, summary.counts.harm) == (3, 1, 1)
    views = {v.weighting: v for v in summary.views}
    assert views[Weighting.DESIGN].shares.benefit == 8 / 12
    assert views[Weighting.DESIGN].leading is Direction.BENEFIT
    assert views[Weighting.SAMPLE_SIZE].shares.none == 18000 / 24420
    assert views[Weighting.SAMPLE_SIZE].leading is Direction.NONE
    assert views[Weighting.SAMPLE_SIZE].changes_leading
    assert remission.summary.thin
    assert remission.hub == "outcome:Synthetic remission" and remission.higher_is_better


def test_a_trial_s_papers_are_one_study_led_by_the_earliest(tmp_path: Path) -> None:
    export = build(tmp_path)
    trial = next(s for s in export.studies if s.key == TRIAL)

    assert trial.pmids == ["999000102", "999000112"]
    assert (trial.title, trial.sample_size, trial.weight) == (
        "Synthetic study 102 of supplement X and mood",
        18000,
        3,
    )
    assert trial.trial_ids == [TRIAL]
    headlines = [n.id for n in findings_of(export) if n.study == TRIAL and n.headline]
    assert headlines == ["999000102-1"]


def test_excluded_designs_are_left_out_and_reviews_are_listed_newest_first(
    tmp_path: Path,
) -> None:
    export = build(tmp_path)

    assert [s.key for s in export.studies] == [
        "999000101",
        "999000103",
        "999000104",
        "999000105",
        "999000106",
        "999000108",
        TRIAL,
    ]
    other = next(s for s in export.studies if s.key == "999000106")
    assert other.weight is None
    assert [(r.pmid, r.design, r.summary) for r in export.reviews] == [
        ("999000202", Design.NARRATIVE_REVIEW, None),
        ("999000201", Design.META_ANALYSIS, "Synthetic pooled benefit on symptoms."),
    ]


def test_every_finding_on_a_mapped_outcome_is_a_node_with_its_quote(tmp_path: Path) -> None:
    # Eleven findings: 107's is an excluded design and 103's distress is out of the domain.
    nodes = findings_of(build(tmp_path))

    assert len(nodes) == 9
    assert all(n.quote.startswith("Synthetic sentence") for n in nodes)
    remission = next(n for n in nodes if n.outcome == REMISSION)
    assert (remission.id, remission.direction, remission.headline) == (
        "999000101-2",
        Direction.BENEFIT,
        True,
    )
    subgroup = next(n for n in nodes if n.study == "999000104")
    assert not subgroup.headline and subgroup.subgroup == "women"
    assert subgroup.dimensions == {"synthetic_dose": 50.0}


def test_genuine_pairs_are_edges_and_apparent_ones_are_listed(tmp_path: Path) -> None:
    export = build(tmp_path)
    edges = [e for e in export.graph.edges if isinstance(e, ContradictionEdge)]

    # The trial pair: rct and rct weigh 3 + 3, and 120 + 18,000 people. The survey pair: 1 + 3
    # and 640 + 60.
    assert [(e.source, e.target, e.design_weight, e.sample_size) for e in edges] == [
        ("999000101-1", "999000102-1", 6, 18120),
        ("999000105-1", "999000108-1", 4, 700),
    ]
    assert edges[0].also_differs == [Reason.INTERVENTION]
    symptoms = export.outcomes[0]
    assert (symptoms.candidate_pairs, symptoms.classified_pairs) == (CANDIDATES, 3)
    assert [(p.study_a, p.study_b, p.finding_a, p.finding_b) for p in symptoms.apparent] == [
        ("999000103", "999000106", "999000103-1", "999000106-1")
    ]
    hubs = {n.id for n in export.graph.nodes if isinstance(n, HubNode)}
    outcome_edges = [e for e in export.graph.edges if isinstance(e, OutcomeEdge)]
    assert {e.target for e in outcome_edges} == hubs
    assert len(outcome_edges) == 9


def test_the_method_counts_drops_and_costs(tmp_path: Path) -> None:
    method = build(tmp_path).method

    assert (method.records_found, method.abstracts_kept) == (14, 11)
    assert (method.findings, method.studies) == (9, 7)
    assert (method.candidate_pairs, method.classified_pairs, method.depth) == (
        CANDIDATES,
        3,
        Arm.SINGLE_SMALL,
    )
    drops = {d.what: d.count for d in method.drops}
    assert drops["records without an abstract"] == 2
    assert drops["book records"] == 1
    assert drops["findings whose quote was not verbatim"] == 1
    assert drops["findings on outcomes the mapping review left out of the domain"] == 1
    assert drops["findings from designs the question excludes: case report, animal, in vitro"] == 1
    assert [(c.stage, c.dollars) for c in method.costs] == [("extract", 0.25)]
    assert method.evals == []


def test_an_off_question_pair_is_counted_as_a_drop_and_left_off_the_panel(tmp_path: Path) -> None:
    off = PairClassification(
        pair_id=pair_id("999000101", "999000105"),
        verdict=Verdict.OFF_QUESTION,
        reason=None,
        also_differs=[],
        detail="exposure: a synthetic trait",
        rationale="Synthetic rationale: one exposure is not the supplement.",
    )
    classified = CLASSIFIED_PAIRS.model_copy(update={"classifications": [*CLASSIFIED, off]})
    run_dir = tmp_path / CONFIG.id
    write_run(run_dir)

    export = export_build.build_export(CONFIG, run_dir, MAPPING, classified, tmp_path / "gold")

    drops = {d.what: d.count for d in export.method.drops}
    assert drops["candidate pairs where one finding's exposure is not the question's"] == 1
    assert build(tmp_path / "plain").method.drops[-1].count == 0
    shown = {e.pair_id for e in export.graph.edges if isinstance(e, ContradictionEdge)}
    shown |= {p.pair_id for o in export.outcomes for p in o.apparent}
    assert off.pair_id not in shown
    assert export.outcomes[0].classified_pairs == 4


def test_classified_pairs_from_another_comparison_are_refused(tmp_path: Path) -> None:
    run_dir = tmp_path / CONFIG.id
    write_run(run_dir)
    stale = ClassifiedPairs(depth=None, candidates=CANDIDATES + 1, classifications=[])

    with pytest.raises(ValueError, match="not from this comparison"):
        export_build.build_export(CONFIG, run_dir, MAPPING, stale, tmp_path / "gold")


LABEL = """\
pmid = "{pmid}"
design = "rct"
sample_size = 120

[[finding]]
quote = "Synthetic sentence 1 from synthetic abstract {pmid}."
outcome_reported = "synthetic mood score"
effect = "no_change"
is_primary = true
is_subgroup = false
dimensions = {dimensions}
"""


def test_a_dimension_is_evaluated_when_every_labeled_finding_has_it(tmp_path: Path) -> None:
    labels = tmp_path / "labels"
    labels.mkdir()
    both = '{ synthetic_dose = 50.0, synthetic_status = "not_reported" }'
    dose = "{ synthetic_dose = 50.0 }"
    (labels / "999000001.toml").write_text(LABEL.format(pmid="999000001", dimensions=both))
    (labels / "999000002.toml").write_text(LABEL.format(pmid="999000002", dimensions=dose))

    assert export_build.evaluated_dimensions(tmp_path) == {"synthetic_dose"}
    assert export_build.evaluated_dimensions(tmp_path / "missing") == set()


def test_a_dimension_s_label_keeps_one_letter_names_upper_case() -> None:
    assert export_build.dimension_label("baseline_vitamin_d_status") == "Baseline vitamin D status"
    assert export_build.dimension_label("synthetic_dose") == "Synthetic dose"


def test_extraction_rows_are_rates_over_their_denominators() -> None:
    scores = ExtractionScores(
        model="synthetic",
        abstracts=4,
        invalid=0,
        design=Count(agree=3, of=4),
        design_answers_alone=Count(agree=3, of=4),
        design_publication_types_first=Count(agree=3, of=4),
        sample_size=Count(agree=2, of=4),
        findings=[
            FieldScore(name="effect", correct=4, model=5, gold=8),
            FieldScore(name="synthetic_dose", correct=0, model=0, gold=0),
        ],
        quotes_checked=5,
        quote_misses=1,
        quotes_dropped=0,
        off_topic_empty=Count(agree=0, of=0),
        design_other=0,
        wrong_designs=[],
    )

    rows = [(r.metric, r.value, r.n) for r in export_build.extraction_evals(scores)]

    # The dose rows have no findings to score, so they are left out.
    assert rows == [
        ("design", 3 / 4, 4),
        ("sample size", 2 / 4, 4),
        ("effect precision", 4 / 5, 5),
        ("effect recall", 4 / 8, 8),
        ("quotes verbatim on the first output", 4 / 5, 5),
    ]


def test_normalization_rows_are_merge_precision_and_recall() -> None:
    merges = MergeCounts(pairs=100, labeled=40, proposed=20, both=18)
    scores = NormalizationScores(
        strings=15,
        outcomes=2,
        labeled_out=1,
        proposed_out=1,
        both_out=1,
        all_pairs=merges,
        drawn=merges,
        strata=[],
    )

    rows = [(r.metric, r.value, r.n) for r in export_build.normalization_evals(scores)]

    assert rows == [("merge precision", 18 / 20, 20), ("merge recall", 18 / 40, 40)]
