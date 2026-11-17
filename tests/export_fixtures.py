"""Synthetic exports for the page: every record here is invented, and every PMID is 999-prefixed.

The committed fixture files under site/src/fixtures are written from these builders; a test
keeps them in step. The summary numbers are worked by hand from the study table below.
"""

from datetime import date
from typing import Any

from claimgraph.compare import Direction
from claimgraph.export import (
    SCHEMA_VERSION,
    ApparentPair,
    ContradictionEdge,
    DesignCount,
    Dimension,
    DirectionCounts,
    DirectionShares,
    Drop,
    EvalResult,
    Export,
    FindingNode,
    Graph,
    HubNode,
    Method,
    Outcome,
    OutcomeEdge,
    Question,
    Review,
    StageCost,
    Study,
    Summary,
    WeightedView,
    Weighting,
)
from claimgraph.models import DESIGN_WEIGHTS, Arm, Design, PairKind, PairSelection, Reason

B, N, H = Direction.BENEFIT, Direction.NONE, Direction.HARM

# key: (design, sample size, extra papers, trial IDs)
STUDIES: dict[str, tuple[Design, int | None, list[str], list[str]]] = {
    "999000101": (Design.RCT, 120, [], []),
    "999000102": (Design.RCT, 18000, ["999000112"], ["NCT99999901"]),
    "999000103": (Design.COHORT, 5600, [], []),
    "999000104": (Design.RCT, 240, [], []),
    "999000105": (Design.CROSS_SECTIONAL, 640, [], []),
    "999000106": (Design.CASE_SERIES, 18, [], []),
    "999000107": (Design.OTHER, None, [], []),
    "999000108": (Design.COHORT, 2100, [], []),
    "999000109": (Design.COHORT, 900, [], []),
    "999000110": (Design.RCT, 60, [], []),
    "999000111": (Design.RCT, 50, [], []),
}

SYMPTOMS, CASES, REMISSION = "Synthetic symptoms", "Synthetic cases", "Synthetic remission"

# (outcome, study, direction, headline, subgroup)
FINDINGS: list[tuple[str, str, Direction, bool, str | None]] = [
    (SYMPTOMS, "999000101", B, True, None),
    (SYMPTOMS, "999000101", N, False, "adults over 65"),
    (SYMPTOMS, "999000102", N, True, None),
    (SYMPTOMS, "999000103", B, True, None),
    (SYMPTOMS, "999000104", B, True, None),
    (SYMPTOMS, "999000105", H, True, None),
    (SYMPTOMS, "999000106", H, True, None),
    (SYMPTOMS, "999000107", N, True, None),
    (SYMPTOMS, "999000108", B, False, "women"),
    (CASES, "999000103", B, True, None),
    (CASES, "999000105", N, True, None),
    (CASES, "999000109", H, True, None),
    (REMISSION, "999000101", B, True, None),
    (REMISSION, "999000102", N, True, None),
    (REMISSION, "999000104", N, True, None),
    (REMISSION, "999000110", B, True, None),
    (REMISSION, "999000111", N, True, None),
]

# (outcome, study a, study b, kind, reason, also differs, detail)
GENUINE: list[tuple[str, str, str, PairKind, Reason, list[Reason], str]] = [
    (
        SYMPTOMS,
        "999000101",
        "999000102",
        PairKind.EFFECT_VS_NONE,
        Reason.POPULATION,
        [Reason.INTERVENTION],
        "synthetic: depressed adults vs a general older population",
    ),
    (
        SYMPTOMS,
        "999000102",
        "999000104",
        PairKind.EFFECT_VS_NONE,
        Reason.INTERVENTION,
        [],
        "synthetic: 400 units daily vs 4,000 units daily",
    ),
    (
        SYMPTOMS,
        "999000102",
        "999000103",
        PairKind.EFFECT_VS_NONE,
        Reason.DESIGN_OR_BIAS,
        [Reason.POPULATION],
        "synthetic: an observed association vs a randomized test",
    ),
    (
        SYMPTOMS,
        "999000104",
        "999000105",
        PairKind.BENEFIT_VS_HARM,
        Reason.COMPARATOR,
        [],
        "synthetic: placebo vs no supplement",
    ),
    (
        SYMPTOMS,
        "999000103",
        "999000105",
        PairKind.BENEFIT_VS_HARM,
        Reason.OUTCOME_MEASURE,
        [Reason.DESIGN_OR_BIAS],
        "synthetic: a symptom scale vs a single mood item",
    ),
    (
        SYMPTOMS,
        "999000101",
        "999000105",
        PairKind.BENEFIT_VS_HARM,
        Reason.UNCLEAR,
        [],
        "synthetic: no difference stands out",
    ),
]

# (outcome, study a, study b, kind, reason, detail)
APPARENT: list[tuple[str, str, str, PairKind, Reason, str]] = [
    (
        SYMPTOMS,
        "999000102",
        "999000106",
        PairKind.EFFECT_VS_NONE,
        Reason.DESIGN_OR_BIAS,
        "underpowered: a harm seen in 18 people",
    ),
    (
        SYMPTOMS,
        "999000101",
        "999000106",
        PairKind.BENEFIT_VS_HARM,
        Reason.OUTCOME_MEASURE,
        "synthetic: the case series measured fatigue, not mood",
    ),
    (
        REMISSION,
        "999000101",
        "999000111",
        PairKind.EFFECT_VS_NONE,
        Reason.DESIGN_OR_BIAS,
        "underpowered: n = 50",
    ),
    (
        REMISSION,
        "999000102",
        "999000110",
        PairKind.EFFECT_VS_NONE,
        Reason.POPULATION,
        "synthetic: remission among the already depressed only",
    ),
]

# Counted studies per outcome, from the tables above, worked by hand.
SUMMARIES = {
    # Benefit 101, 103, 104; none 102; harm 105, 106. 107 has no design weight: uncounted.
    # Design: benefit 3 + 2 + 3 = 8, none 3, harm 1 + 0.5 = 1.5, of 12.5.
    # Sample size: benefit 120 + 5,600 + 240 = 5,960, none 18,000, harm 658, of 24,618.
    SYMPTOMS: Summary(
        studies=6,
        counts=DirectionCounts(benefit=3, none=1, harm=2),
        designs=[
            DesignCount(design=Design.RCT, studies=3),
            DesignCount(design=Design.COHORT, studies=1),
            DesignCount(design=Design.CROSS_SECTIONAL, studies=1),
            DesignCount(design=Design.CASE_SERIES, studies=1),
        ],
        thin=False,
        views=[
            WeightedView(
                weighting=Weighting.DESIGN,
                studies=6,
                shares=DirectionShares(benefit=8 / 12.5, none=3 / 12.5, harm=1.5 / 12.5),
                leading=B,
                changes_leading=False,
            ),
            WeightedView(
                weighting=Weighting.UNWEIGHTED,
                studies=6,
                shares=DirectionShares(benefit=3 / 6, none=1 / 6, harm=2 / 6),
                leading=B,
                changes_leading=False,
            ),
            WeightedView(
                weighting=Weighting.SAMPLE_SIZE,
                studies=6,
                shares=DirectionShares(benefit=5960 / 24618, none=18000 / 24618, harm=658 / 24618),
                leading=N,
                changes_leading=True,
            ),
        ],
        subgroup_only=1,
        uncounted=1,
    ),
    CASES: Summary(
        studies=3,
        counts=DirectionCounts(benefit=1, none=1, harm=1),
        designs=[
            DesignCount(design=Design.COHORT, studies=2),
            DesignCount(design=Design.CROSS_SECTIONAL, studies=1),
        ],
        thin=True,
        views=[],
        subgroup_only=0,
        uncounted=0,
    ),
    # Benefit 101, 110; none 102, 104, 111. Design: benefit 6, none 9, of 15.
    # Sample size: benefit 180, none 18,290, of 18,470.
    REMISSION: Summary(
        studies=5,
        counts=DirectionCounts(benefit=2, none=3, harm=0),
        designs=[DesignCount(design=Design.RCT, studies=5)],
        thin=False,
        views=[
            WeightedView(
                weighting=Weighting.DESIGN,
                studies=5,
                shares=DirectionShares(benefit=6 / 15, none=9 / 15, harm=0),
                leading=N,
                changes_leading=False,
            ),
            WeightedView(
                weighting=Weighting.UNWEIGHTED,
                studies=5,
                shares=DirectionShares(benefit=2 / 5, none=3 / 5, harm=0),
                leading=N,
                changes_leading=False,
            ),
            WeightedView(
                weighting=Weighting.SAMPLE_SIZE,
                studies=5,
                shares=DirectionShares(benefit=180 / 18470, none=18290 / 18470, harm=0),
                leading=N,
                changes_leading=False,
            ),
        ],
        subgroup_only=0,
        uncounted=0,
    ),
}
# Pairs of headline findings with differing directions, from FINDINGS. On symptoms: benefit
# 101, 103, 104 against none 102, 107 makes 6, against harm 105, 106 makes 6, and none against
# harm makes 4.
CANDIDATES = {SYMPTOMS: 16, CASES: 3, REMISSION: 6}

DIMENSIONS = [
    Dimension(
        name="baseline_synthetic_status",
        label="Baseline synthetic status",
        short_label="Baseline status",
        refines=Reason.POPULATION,
        unit=None,
        evaluated=False,
    ),
    Dimension(
        name="synthetic_dose",
        label="Synthetic dose",
        refines=Reason.INTERVENTION,
        unit="mg",
        evaluated=True,
    ),
]


def _hub(outcome: str) -> str:
    return f"outcome:{outcome}"


def _finding_id(study: str, n: int) -> str:
    return f"{study}-{n}"


def _findings() -> list[FindingNode]:
    nodes: list[FindingNode] = []
    seen: dict[str, int] = {}
    for outcome, study, direction, headline, subgroup in FINDINGS:
        seen[study] = seen.get(study, 0) + 1
        n = seen[study]
        words = {B: "lowered", N: "did not change", H: "raised"}[direction]
        nodes.append(
            FindingNode(
                kind="finding",
                id=_finding_id(study, n),
                outcome=outcome,
                study=study,
                pmid=study,
                direction=direction,
                headline=headline,
                claim=f"Synthetic supplement X {words} {outcome.lower()} in synthetic study "
                f"{study[-3:]}.",
                quote=f"Synthetic sentence {n} from synthetic abstract {study}.",
                outcome_reported=f"synthetic {outcome.lower()} score",
                is_primary=headline and n == 1,
                subgroup=subgroup,
                population="Synthetic adults",
                intervention="Synthetic supplement X, 50 mg daily for 8 weeks",
                comparator="Synthetic placebo",
                dimensions={
                    "baseline_synthetic_status": "not_reported",
                    "synthetic_dose": 50.0 if study != "999000107" else None,
                },
            )
        )
    return nodes


def _headlines(nodes: list[FindingNode]) -> dict[tuple[str, str], FindingNode]:
    return {(n.outcome, n.study): n for n in nodes if n.headline}


def _weight(study: str) -> float:
    return DESIGN_WEIGHTS.get(STUDIES[study][0], 0)


def _size(a: str, b: str) -> int | None:
    sa, sb = STUDIES[a][1], STUDIES[b][1]
    return None if sa is None or sb is None else sa + sb


def _studies() -> list[Study]:
    return [
        Study(
            key=key,
            pmids=[key, *extra],
            title=f"Synthetic study {key[-3:]} of supplement X and mood",
            first_author=None if key == "999000107" else f"Synthetic{key[-3:]}",
            journal="Synthetic Journal",
            year=2010 + int(key[-2:]),
            design=design,
            weight=DESIGN_WEIGHTS.get(design),
            sample_size=size,
            trial_ids=trials,
        )
        for key, (design, size, extra, trials) in STUDIES.items()
    ]


def synthetic() -> Export:
    nodes = _findings()
    headline = _headlines(nodes)
    outcomes = [SYMPTOMS, CASES, REMISSION]
    edges: list[OutcomeEdge | ContradictionEdge] = [
        OutcomeEdge(kind="outcome", source=n.id, target=_hub(n.outcome)) for n in nodes
    ]
    for outcome, a, b, kind, reason, also, detail in GENUINE:
        edges.append(
            ContradictionEdge(
                kind="contradiction",
                source=headline[(outcome, a)].id,
                target=headline[(outcome, b)].id,
                pair_id=f"{outcome}|{a}|{b}",
                pair_kind=kind,
                reason=reason,
                also_differs=also,
                detail=detail,
                rationale=f"Synthetic rationale: the studies differ most in {reason.value}.",
                design_weight=_weight(a) + _weight(b),
                sample_size=_size(a, b),
            )
        )
    classified = {o: 0 for o in outcomes}
    for outcome in [g[0] for g in GENUINE] + [a[0] for a in APPARENT]:
        classified[outcome] += 1
    return Export(
        schema_version=SCHEMA_VERSION,
        question=Question(
            id="synthetic-question",
            title="Does synthetic supplement X affect synthetic mood?",
            retrieved=date(2026, 1, 15),
            abstracts=40,
            mapping_reviewed=True,
            dimensions=DIMENSIONS,
        ),
        outcomes=[
            Outcome(
                name=o,
                hub=_hub(o),
                higher_is_better=o == REMISSION,
                summary=SUMMARIES[o],
                candidate_pairs=CANDIDATES[o],
                classified_pairs=classified[o],
                apparent=[
                    ApparentPair(
                        pair_id=f"{o}|{a}|{b}",
                        pair_kind=kind,
                        study_a=a,
                        study_b=b,
                        finding_a=headline[(o, a)].id,
                        finding_b=headline[(o, b)].id,
                        reason=reason,
                        detail=detail,
                    )
                    for outcome, a, b, kind, reason, detail in APPARENT
                    if outcome == o
                ],
            )
            for o in outcomes
        ],
        studies=_studies(),
        graph=Graph(
            directed=False,
            multigraph=False,
            graph={},
            nodes=[HubNode(kind="hub", id=_hub(o), outcome=o) for o in outcomes] + nodes,
            edges=edges,
        ),
        reviews=[
            Review(
                pmid="999000201",
                title="Synthetic systematic review of supplement X and mood",
                first_author="Synthetic201",
                journal="Synthetic Reviews",
                year=2024,
                design=Design.META_ANALYSIS,
                summary="Pooled 12 synthetic trials: a small synthetic benefit on symptoms.",
            ),
            Review(
                pmid="999000202",
                title="Synthetic narrative review of supplement X",
                first_author=None,
                journal="Synthetic Reviews",
                year=2019,
                design=Design.NARRATIVE_REVIEW,
                summary=None,
            ),
        ],
        method=Method(
            records_found=52,
            abstracts_kept=40,
            findings=len(nodes),
            studies=len(STUDIES),
            drops=[
                Drop(what="records without an abstract", count=8),
                Drop(what="records not in English", count=4),
                Drop(what="findings outside the outcome domain", count=3),
                Drop(what="findings whose quote was not verbatim", count=1),
            ],
            candidate_pairs=sum(CANDIDATES.values()),
            classified_pairs=sum(classified.values()),
            depth=Arm.SINGLE_SMALL,
            selection=PairSelection(minimum=3, fields=5, size_ratio=3, per_study=8),
            costs=[
                StageCost(stage="extract", dollars=0.11),
                StageCost(stage="normalize", dollars=0.02),
                StageCost(stage="classify", dollars=0.05),
            ],
            evals=[
                EvalResult(
                    eval="extraction", metric="direction", value=0.9, n=32, low=0.78, high=0.97
                ),
                EvalResult(
                    eval="normalization",
                    metric="merge precision",
                    value=0.95,
                    n=200,
                    low=None,
                    high=None,
                ),
            ],
        ),
    )


def minimal() -> Export:
    """One thin outcome, two studies that agree, no reviews, no dimensions and stub depth."""
    studies = ["999000301", "999000302"]
    nodes = [
        FindingNode(
            kind="finding",
            id=f"{s}-1",
            outcome=SYMPTOMS,
            study=s,
            pmid=s,
            direction=B,
            headline=True,
            claim=f"Synthetic supplement X lowered synthetic symptoms in study {s[-3:]}.",
            quote=f"Synthetic sentence from abstract {s}.",
            outcome_reported="synthetic symptom score",
            is_primary=True,
            subgroup=None,
            population="Synthetic adults",
            intervention="Synthetic supplement X",
            comparator="Synthetic placebo",
            dimensions={},
        )
        for s in studies
    ]
    return Export(
        schema_version=SCHEMA_VERSION,
        question=Question(
            id="synthetic-minimal",
            title="Does synthetic supplement Y affect synthetic mood?",
            retrieved=date(2026, 1, 15),
            abstracts=2,
            mapping_reviewed=True,
            dimensions=[],
        ),
        outcomes=[
            Outcome(
                name=SYMPTOMS,
                hub=_hub(SYMPTOMS),
                higher_is_better=False,
                summary=Summary(
                    studies=2,
                    counts=DirectionCounts(benefit=2, none=0, harm=0),
                    designs=[DesignCount(design=Design.RCT, studies=2)],
                    thin=True,
                    views=[],
                    subgroup_only=0,
                    uncounted=0,
                ),
                candidate_pairs=0,
                classified_pairs=0,
                apparent=[],
            )
        ],
        studies=[
            Study(
                key=s,
                pmids=[s],
                title=f"Synthetic trial {s[-3:]} of supplement Y",
                first_author=f"Synthetic{s[-3:]}",
                journal="Synthetic Journal",
                year=2021,
                design=Design.RCT,
                weight=3,
                sample_size=100,
                trial_ids=[],
            )
            for s in studies
        ],
        graph=Graph(
            directed=False,
            multigraph=False,
            graph={},
            nodes=[HubNode(kind="hub", id=_hub(SYMPTOMS), outcome=SYMPTOMS), *nodes],
            edges=[OutcomeEdge(kind="outcome", source=n.id, target=_hub(SYMPTOMS)) for n in nodes],
        ),
        reviews=[],
        method=Method(
            records_found=2,
            abstracts_kept=2,
            findings=2,
            studies=2,
            drops=[],
            candidate_pairs=0,
            classified_pairs=0,
            depth=None,
            costs=[],
            evals=[],
        ),
    )


FIXTURES: dict[str, Any] = {"synthetic": synthetic, "minimal": minimal}
