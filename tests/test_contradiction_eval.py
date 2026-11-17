"""Scoring the arms against the contradiction labels, on synthetic pairs."""

from pathlib import Path

import pytest
from test_label_set import answer, pair, sample

from claimgraph import contradiction_eval, label_set
from claimgraph.contradiction_eval import Difference, Interval
from claimgraph.labels import ContradictionLabel, load_contradiction_pairs
from claimgraph.models import Arm, PairClassification, Reason, Verdict
from claimgraph.pair_sample import DrawnPair

ROUNDS = 200
COSTS = {Arm.SINGLE_SMALL: (0.0001, 3000.0), Arm.COUNCIL: (0.01, 12000.0)}


def label(
    p: DrawnPair,
    verdict: Verdict = Verdict.GENUINE,
    reason: Reason | None = Reason.POPULATION,
    also: list[Reason] | None = None,
) -> ContradictionLabel:
    return ContradictionLabel(
        outcome=p.outcome,
        study_a=p.study_a,
        study_b=p.study_b,
        verdict=verdict,
        reason=reason,
        also_differs=also or [],
    )


def off(p: DrawnPair) -> PairClassification:
    return PairClassification(
        pair_id=p.pair_id,
        verdict=Verdict.OFF_QUESTION,
        reason=None,
        also_differs=[],
        detail="exposure: synthetic",
        rationale="Synthetic.",
    )


def setup(
    council_right: set[int], small_right: set[int], agreeing: int = 4, disagreeing: int = 4
) -> tuple[list[DrawnPair], label_set.LabelSet, dict[Arm, dict[str, PairClassification]]]:
    """Pairs 0..disagreeing-1 disagree; the rest agree. Every label is genuine, population."""
    pairs = [pair(n) for n in range(disagreeing + agreeing)]
    answers: dict[Arm, dict[str, PairClassification]] = {a: {} for a in COSTS}
    for n, p in enumerate(pairs):
        for arm, right in ((Arm.COUNCIL, council_right), (Arm.SINGLE_SMALL, small_right)):
            agree = n >= disagreeing
            correct = agree or n in right
            reason = Reason.POPULATION if correct else Reason.DESIGN_OR_BIAS
            if not agree and arm is Arm.SINGLE_SMALL and n in council_right and n in small_right:
                reason = Reason.POPULATION
            answers[arm][p.pair_id] = answer(p, reason=reason)
    # Make every disagreement pair differ between the arms, whoever is right.
    for n in range(disagreeing):
        if (n in council_right) == (n in small_right):
            answers[Arm.SINGLE_SMALL][pairs[n].pair_id] = answer(pairs[n], reason=Reason.UNCLEAR)
    found = label_set.build(sample(pairs), answers, seed=7, cap=2, agreement=2)
    return pairs, found, answers


def test_a_labeled_pair_weighs_its_group_over_its_group_s_labels() -> None:
    pairs, found, _ = setup({0, 1, 2, 3}, set())
    labels = [label(p) for p in pairs]

    units, sizes = contradiction_eval.units(pairs, found, labels)

    assert sizes == {"synthetic symptoms | effect_vs_none | trial_trial": 4, "agreement": 4}
    assert sorted(u.weight for u in units) == [2.0, 2.0, 2.0, 2.0]
    # A pair left out leaves its group's count, so the weights stay exact.
    dropped = found.pair_ids[0]
    units, sizes = contradiction_eval.units(pairs, found, labels, left_out={dropped})
    assert sorted(u.weight for u in units) == [2.0, 2.0, 3.0]


def test_strict_needs_the_primary_reason_and_lenient_takes_also_differs() -> None:
    p = pair(1)
    given = answer(p, reason=Reason.DESIGN_OR_BIAS)
    labeled = label(p, also=[Reason.DESIGN_OR_BIAS])

    assert not contradiction_eval.RULES["correct"](given, labeled)
    assert contradiction_eval.RULES["correct_lenient"](given, labeled)
    # An arm's off_question on a pair the label puts on the question is wrong both ways.
    assert not contradiction_eval.RULES["correct_lenient"](off(p), labeled)


def test_off_question_labels_are_left_out_and_counted() -> None:
    pairs, found, answers = setup({0, 1, 2, 3}, set())
    labels = [label(p) for p in pairs]
    first = found.pair_ids[0]
    labels = [
        label(p, Verdict.OFF_QUESTION, None) if p.pair_id == first else label(p) for p in pairs
    ]

    figures = contradiction_eval.score(pairs, found, labels, answers, COSTS, rounds=ROUNDS)

    assert (figures.labeled, figures.off_question_labels, figures.scored) == (4, 1, 3)
    assert figures.on_question == 6.0


def test_agreement_pairs_count_in_the_rate_but_not_in_a_paired_difference() -> None:
    # Council is right on both drawn disagreement pairs, single-small on neither; both are
    # right on the agreement pairs. Half the pairs disagree, so council leads by 50 points.
    pairs, found, answers = setup({0, 1, 2, 3}, set())
    labels = [label(p) for p in pairs]

    figures = contradiction_eval.score(pairs, found, labels, answers, COSTS, rounds=ROUNDS)

    by_arm = {a.arm: a for a in figures.arms}
    assert by_arm[Arm.COUNCIL].correct.estimate == 1.0
    assert by_arm[Arm.SINGLE_SMALL].correct.estimate == 0.5
    (council,) = [d for d in figures.against_small if d.arm is Arm.COUNCIL]
    assert council.difference.estimate == 0.5
    assert figures.scope.best is Arm.COUNCIL


def test_the_same_seed_gives_the_same_intervals() -> None:
    pairs, found, answers = setup({0, 2}, {1, 3})
    labels = [label(p) for p in pairs]

    first = contradiction_eval.score(pairs, found, labels, answers, COSTS, rounds=ROUNDS)
    again = contradiction_eval.score(pairs, found, labels, answers, COSTS, rounds=ROUNDS)

    assert first == again


def difference(arm: Arm, against: Arm, low: float, high: float) -> Difference:
    middle = (low + high) / 2
    return Difference(
        arm=arm, against=against, difference=Interval(estimate=middle, low=low, high=high)
    )


@pytest.mark.parametrize(
    ("gain", "close", "arm", "every_pair"),
    [
        ((-0.01, 0.2), (-0.1, 0.0), Arm.SINGLE_SMALL, True),
        ((0.05, 0.2), (-0.1, 0.01), Arm.SINGLE_MID, False),
        ((0.05, 0.2), (-0.2, -0.05), Arm.COUNCIL, False),
    ],
)
def test_the_scope_rule_takes_single_small_unless_the_gain_is_clear(
    gain: tuple[float, float], close: tuple[float, float], arm: Arm, every_pair: bool
) -> None:
    scores = [
        contradiction_eval.ArmScore(
            arm=a,
            correct=Interval(estimate=0.5, low=0.4, high=0.6),
            correct_lenient=Interval(estimate=0.5, low=0.4, high=0.6),
            verdict=Interval(estimate=0.5, low=0.4, high=0.6),
            precision_genuine=Interval(estimate=0.5, low=0.4, high=0.6),
            called_off_question=0,
            usd_per_pair=usd,
            billed_per_pair=1.0,
        )
        for a, usd in ((Arm.SINGLE_SMALL, 0.0001), (Arm.SINGLE_MID, 0.001), (Arm.COUNCIL, 0.01))
    ]
    against_best = [
        difference(Arm.SINGLE_SMALL, Arm.COUNCIL, -0.3, -0.1),
        difference(Arm.SINGLE_MID, Arm.COUNCIL, *close),
        Difference(
            arm=Arm.COUNCIL, against=Arm.COUNCIL, difference=Interval(estimate=0, low=0, high=0)
        ),
    ]

    pick = contradiction_eval.scope_pick(
        scores, Arm.COUNCIL, difference(Arm.COUNCIL, Arm.SINGLE_SMALL, *gain), against_best
    )

    assert (pick.arm, pick.every_pair) == (arm, every_pair)


def test_labels_before_a_correction_are_its_was_values(tmp_path: Path) -> None:
    p = pair(1)
    (tmp_path / "corrections.toml").write_text(
        f"""
[[correction]]
outcome = "{p.outcome}"
study_a = "{p.study_a}"
study_b = "{p.study_b}"
was_verdict = "genuine"
was_reason = "population"
was_also_differs = []
found = "after_scoring"
rule = "Synthetic rule."
"""
    )
    corrections = contradiction_eval.load_corrections(tmp_path / "corrections.toml")
    now = [label(p, reason=Reason.DESIGN_OR_BIAS)]

    after = contradiction_eval.Found.AFTER_SCORING
    assert contradiction_eval.before(now, corrections, after)[0].reason is Reason.POPULATION
    assert (
        contradiction_eval.before(now, corrections, contradiction_eval.Found.BEFORE_SCORING) == now
    )


def test_the_committed_corrections_match_the_labels() -> None:
    root = Path(__file__).parents[1] / "evals" / "gold" / "vitamin-d-depression"
    corrections = contradiction_eval.load_corrections(root / contradiction_eval.CORRECTIONS_FILE)
    labeled = {p.pair_id: p for p in load_contradiction_pairs(root / "contradiction_pairs.toml")}

    # Each correction names a labeled pair and changed it.
    for c in corrections:
        now = labeled[c.pair_id]
        assert (now.verdict, now.reason) != (c.was_verdict, c.was_reason)


def scores_with(
    small: tuple[float, float], council: tuple[float, float]
) -> list[contradiction_eval.ArmScore]:
    def one(arm: Arm, low: float, high: float) -> contradiction_eval.ArmScore:
        middle = Interval(estimate=(low + high) / 2, low=low, high=high)
        return contradiction_eval.ArmScore(
            arm=arm,
            verdict=middle,
            correct=middle,
            correct_lenient=middle,
            precision_genuine=middle,
            called_off_question=0,
            usd_per_pair=0.0,
            billed_per_pair=0.0,
        )

    return [one(Arm.SINGLE_SMALL, *small), one(Arm.COUNCIL, *council)]


@pytest.mark.parametrize(
    ("small", "arm", "every_pair"),
    [((0.79, 0.876), Arm.COUNCIL, False), ((0.79, 0.90), Arm.SINGLE_SMALL, True)],
)
def test_the_ruling_keeps_single_small_unless_its_precision_is_clearly_below(
    small: tuple[float, float], arm: Arm, every_pair: bool
) -> None:
    pairs, found, answers = setup({0, 1, 2, 3}, set())
    figures = contradiction_eval.score(
        pairs, found, [label(p) for p in pairs], answers, COSTS, rounds=ROUNDS
    )
    primary = figures.model_copy(
        update={"scope": figures.scope.model_copy(update={"arm": Arm.COUNCIL, "every_pair": False})}
    )
    sensitivity = figures.model_copy(
        update={
            "arms": scores_with(small, (0.887, 0.97)),
            "scope": figures.scope.model_copy(update={"arm": Arm.SINGLE_SMALL, "every_pair": True}),
        }
    )

    pick = contradiction_eval.hosted_pick(primary, sensitivity)

    assert (pick.arm, pick.every_pair) == (arm, every_pair)
