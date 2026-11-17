import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from claimgraph import pair_sample
from claimgraph.models import Comparison, Design, Effect, ExposureSide, Finding, PairKind
from claimgraph.pair_sample import Combination, DrawnPair, PairSample, combination

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "pair_sample" / pair_sample.SAMPLE_FILE


def drawn(a: str, b: str, combo: Combination = Combination.TRIAL_TRIAL) -> DrawnPair:
    return DrawnPair(
        outcome="Synthetic symptoms",
        study_a=a,
        study_b=b,
        kind=PairKind.EFFECT_VS_NONE,
        combination=combo,
        finding_a=f"{a}-1",
        finding_b=f"{b}-1",
    )


def synthetic_sample() -> PairSample:
    """Drawn pairs over the synthetic export's studies, for pages and tests that read the file."""
    return PairSample(
        question="synthetic-question",
        seed=7,
        eval=[
            drawn("999000101", "999000102"),
            drawn("999000102", "999000103", Combination.TRIAL_OBSERVATIONAL),
            drawn("999000103", "999000105", Combination.OBSERVATIONAL_OBSERVATIONAL),
        ],
        dev=[drawn("999000110", "999000111")],
    )


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Design.RCT, Design.NON_RANDOMIZED_TRIAL, Combination.TRIAL_TRIAL),
        (Design.COHORT, Design.RCT, Combination.TRIAL_OBSERVATIONAL),
        (Design.CASE_SERIES, Design.CROSS_SECTIONAL, Combination.OBSERVATIONAL_OBSERVATIONAL),
        (Design.CASE_CONTROL, Design.OTHER, Combination.WITH_OTHER),
        (Design.OTHER, Design.RCT, Combination.WITH_OTHER),
    ],
)
def test_two_designs_make_one_combination(a: Design, b: Design, expected: Combination) -> None:
    assert combination(a, b) is expected
    assert combination(b, a) is expected


def test_reviews_have_no_combination() -> None:
    with pytest.raises(ValueError, match="never paired"):
        combination(Design.META_ANALYSIS, Design.RCT)


def test_a_dev_pair_shares_no_study_with_an_eval_pair() -> None:
    with pytest.raises(ValidationError, match="share no study"):
        PairSample(
            question="synthetic-question",
            seed=1,
            eval=[drawn("999000101", "999000102")],
            dev=[drawn("999000102", "999000103")],
        )


def test_a_pair_is_drawn_once() -> None:
    with pytest.raises(ValidationError, match="drawn once"):
        PairSample(
            question="synthetic-question",
            seed=1,
            eval=[drawn("999000101", "999000102"), drawn("999000101", "999000102")],
            dev=[],
        )


def test_the_committed_synthetic_sample_matches_its_builder() -> None:
    text = synthetic_sample().model_dump_json(indent=2) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text)

    assert FIXTURE.read_text() == text
    assert pair_sample.read_sample(FIXTURE) == synthetic_sample()


BVH, EVN = PairKind.BENEFIT_VS_HARM, PairKind.EFFECT_VS_NONE
TT, TO, OO, WO = (
    Combination.TRIAL_TRIAL,
    Combination.TRIAL_OBSERVATIONAL,
    Combination.OBSERVATIONAL_OBSERVATIONAL,
    Combination.WITH_OTHER,
)


def test_slots_are_shared_equally_with_half_for_pairs_with_another_design() -> None:
    # 70 slots over three full cells and a half-weight one: 20 each, and 10.
    available = {("o", EVN, TT): 100, ("o", EVN, TO): 100, ("o", EVN, OO): 100, ("o", EVN, WO): 100}

    slots = pair_sample.allocate(available, {EVN: 70})

    assert slots == {("o", EVN, TT): 20, ("o", EVN, TO): 20, ("o", EVN, OO): 20, ("o", EVN, WO): 10}


def test_a_cell_that_runs_out_frees_its_slots_for_the_others() -> None:
    available = {("o", BVH, TO): 3, ("o", BVH, OO): 100}

    assert pair_sample.allocate(available, {BVH: 20}) == {("o", BVH, TO): 3, ("o", BVH, OO): 17}


def test_each_pair_kind_fills_only_its_own_cells() -> None:
    available = {("o", BVH, OO): 5, ("o", EVN, OO): 100}

    slots = pair_sample.allocate(available, {BVH: 10, EVN: 10})

    assert slots == {("o", BVH, OO): 5, ("o", EVN, OO): 10}


def pool() -> list[DrawnPair]:
    """Synthetic pairs: 30 effect-vs-none and 10 benefit-vs-harm among 80 studies."""
    pairs: list[DrawnPair] = []
    for i in range(40):
        a, b = f"999{2 * i:06d}", f"999{2 * i + 1:06d}"
        pair = drawn(a, b, TT if i % 2 else OO)
        pairs.append(pair.model_copy(update={"kind": BVH if i < 10 else EVN}))
    return pairs


def test_eval_pairs_are_openly_licensed_and_dev_pairs_share_no_study(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pair_sample, "EVAL_SIZES", {BVH: 4, EVN: 6})
    monkeypatch.setattr(pair_sample, "DEV_SIZES", {BVH: 2, EVN: 4})
    # Even-numbered pairs have both papers open.
    is_open = lambda pmid: int(pmid) // 2 % 2 == 0  # noqa: E731

    sample = pair_sample.draw("synthetic-question", pool(), is_open, seed=3)

    assert len(sample.eval) == 10 and len(sample.dev) == 6
    assert all(is_open(p.study_a) and is_open(p.study_b) for p in sample.eval)
    assert sum(p.kind is BVH for p in sample.eval) == 4


def test_the_same_seed_draws_the_same_pairs() -> None:
    first = pair_sample.draw("synthetic-question", pool(), lambda _: True, seed=5)

    assert (
        pair_sample.draw("synthetic-question", list(reversed(pool())), lambda _: True, seed=5)
        == first
    )


def test_the_eval_papers_are_the_eval_pairs_headline_papers() -> None:
    assert pair_sample.eval_papers(synthetic_sample()) == {
        "999000101",
        "999000102",
        "999000103",
        "999000105",
    }


def test_the_panel_order_is_weight_then_size_then_pair_id() -> None:
    def drawn(a: str, b: str, outcome: str = "synthetic symptoms") -> DrawnPair:
        return DrawnPair(
            outcome=outcome,
            study_a=a,
            study_b=b,
            kind=PairKind.EFFECT_VS_NONE,
            combination=Combination.TRIAL_TRIAL,
            finding_a=f"{a}-1",
            finding_b=f"{b}-1",
        )

    studies: dict[str, tuple[float | None, int | None]] = {
        "999000001": (3.0, 100),
        "999000002": (3.0, None),
        "999000003": (3.0, 50),
        "999000004": (1.0, 5000),
        "999000005": (None, 10),
    }
    sized = drawn("999000001", "999000003")  # weight 6, 150 enrolled
    unsized = drawn("999000001", "999000002")  # weight 6, a size missing: after the sized one
    lighter = drawn("999000001", "999000004")  # weight 4, however large
    unweighted = drawn("999000004", "999000005")  # weight 1: no weight counts as 0
    other = drawn("999000004", "999000005", "synthetic cases")

    pool = [unweighted, lighter, unsized, sized]
    assert pair_sample.panel_order(pool, studies) == [sized, unsized, lighter, unweighted]
    assert other not in pair_sample.panel_order(pool, studies)


def alike_finding(pmid: str, measure: str, status: str | None) -> Finding:
    return Finding(
        finding_id=f"{pmid}-1",
        claim=f"Synthetic finding of synthetic study {pmid[-3:]}.",
        quote=f"Synthetic sentence from synthetic abstract {pmid}.",
        outcome_reported=measure,
        effect=Effect.DECREASED,
        is_primary=True,
        is_subgroup=False,
        subgroup=None,
        population="Synthetic adults",
        intervention="Synthetic supplement X",
        comparator="Synthetic placebo",
        exposure_side=ExposureSide.MORE,
        outcome_direction=Effect.DECREASED,
        comparison=Comparison.BETWEEN,
        domain_outcome="synthetic symptoms",
        dimensions={} if status is None else {"synthetic_status": status},
    )


def alike_pair(a: str, b: str) -> DrawnPair:
    return DrawnPair(
        outcome="synthetic symptoms",
        study_a=a,
        study_b=b,
        kind=PairKind.EFFECT_VS_NONE,
        combination=Combination.TRIAL_TRIAL,
        finding_a=f"{a}-1",
        finding_b=f"{b}-1",
    )


def test_a_field_is_shared_only_when_both_studies_report_it() -> None:
    a = alike_finding("999000001", "synthetic PHQ-9 score", "not_reported")
    b = alike_finding("999000002", "PHQ-9", "not_reported")
    rct = (Design.RCT, Design.RCT)
    # Same design, same instrument, sizes within 3x; two unreported statuses are unknown.
    assert pair_sample.shared_fields(a, b, rct, (100, 300), ["synthetic_status"]) == 3
    assert pair_sample.shared_fields(a, b, rct, (100, 301), ["synthetic_status"]) == 2
    assert pair_sample.shared_fields(a, b, rct, (100, None), ["synthetic_status"]) == 2
    other = (Design.OTHER, Design.OTHER)
    assert pair_sample.shared_fields(a, b, other, (None, None), ["synthetic_status"]) == 1
    low_a = alike_finding("999000001", "synthetic mood", "low")
    low_b = alike_finding("999000002", "synthetic anxiety", "low")
    assert pair_sample.shared_fields(low_a, low_b, rct, (10, 10), ["synthetic_status"]) == 3


def test_the_most_alike_pairs_come_first_and_a_study_is_capped() -> None:
    keys = [f"99900000{i}" for i in range(1, 6)]
    findings = {f"{k}-1": alike_finding(k, "synthetic PHQ-9", "low") for k in keys}
    findings["999000005-1"] = alike_finding("999000005", "synthetic mood", "high")
    designs = dict.fromkeys(keys, Design.RCT)
    studies: dict[str, tuple[float | None, int | None]] = {k: (3.0, 100) for k in keys}
    studies["999000004"] = (3.0, 1000)
    pairs = [
        alike_pair("999000001", "999000002"),  # four of four
        alike_pair("999000001", "999000003"),  # four of four
        alike_pair("999000001", "999000004"),  # three: sizes 10x apart
        alike_pair("999000001", "999000005"),  # two: measure and status differ
        alike_pair("999000002", "999000003"),  # four of four
    ]
    dims = ["synthetic_status"]

    chosen = pair_sample.alike_pairs(pairs, findings, designs, studies, dims, 3)
    assert [p.pair_id for p in chosen] == [
        p.pair_id for p in [pairs[0], pairs[1], pairs[4], pairs[2]]
    ]

    capped = pair_sample.alike_pairs(pairs, findings, designs, studies, dims, 3, per_study=1)
    # 999000001 keeps its first, most alike pair; 999000002 and 999000003 are then both used.
    assert [p.pair_id for p in capped] == [pairs[0].pair_id]
    two = pair_sample.alike_pairs(pairs, findings, designs, studies, dims, 3, per_study=2)
    assert [p.pair_id for p in two] == [p.pair_id for p in [pairs[0], pairs[1], pairs[4]]]
