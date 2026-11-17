"""Choosing the eval pairs to label from the arms' answers, on synthetic pairs."""

from pathlib import Path

import pytest

from claimgraph import label_set
from claimgraph.models import Arm, PairClassification, PairKind, Reason, Verdict
from claimgraph.pair_sample import Combination, DrawnPair, PairSample


def pair(
    n: int, outcome: str = "synthetic symptoms", kind: PairKind = PairKind.EFFECT_VS_NONE
) -> DrawnPair:
    a, b = f"999{n:03d}001", f"999{n:03d}002"
    return DrawnPair(
        outcome=outcome,
        study_a=a,
        study_b=b,
        kind=kind,
        combination=Combination.TRIAL_TRIAL,
        finding_a=f"{a}-1",
        finding_b=f"{b}-1",
    )


def answer(
    p: DrawnPair, verdict: Verdict = Verdict.GENUINE, reason: Reason = Reason.POPULATION
) -> PairClassification:
    return PairClassification(
        pair_id=p.pair_id,
        verdict=verdict,
        reason=reason,
        also_differs=[],
        detail="synthetic detail",
        rationale="Synthetic rationale.",
    )


def sample(pairs: list[DrawnPair]) -> PairSample:
    return PairSample(question="synthetic-question", seed=7, eval=pairs, dev=[])


def answers(pairs: list[DrawnPair], split: set[int]) -> dict[Arm, dict[str, PairClassification]]:
    """Every arm agrees, except that single-mid differs on the pairs numbered in `split`."""
    out: dict[Arm, dict[str, PairClassification]] = {}
    for arm in (Arm.SINGLE_SMALL, Arm.SINGLE_MID, Arm.COUNCIL):
        out[arm] = {}
        for n, p in enumerate(pairs):
            differs = arm is Arm.SINGLE_MID and n in split
            out[arm][p.pair_id] = answer(
                p, reason=Reason.DESIGN_OR_BIAS if differs else Reason.POPULATION
            )
    return out


def test_slots_are_shared_in_proportion_by_largest_remainder() -> None:
    # Quotas 15.5, 6 and 0.5: the spare slot is a tie on the remainder, broken by name.
    assert label_set.proportional({"a": 31, "b": 12, "c": 1}, 22) == {"a": 16, "b": 6, "c": 0}
    assert label_set.proportional({"a": 3, "b": 2}, 10) == {"a": 3, "b": 2}


def test_a_pair_differing_on_the_reason_alone_is_a_disagreement() -> None:
    p = pair(1)
    off = PairClassification(
        pair_id=p.pair_id,
        verdict=Verdict.OFF_QUESTION,
        reason=None,
        also_differs=[],
        detail="exposure: synthetic",
        rationale="Synthetic.",
    )

    assert label_set.disagrees([answer(p), answer(p, reason=Reason.UNCLEAR)])
    assert label_set.disagrees([answer(p), off])
    assert not label_set.disagrees([answer(p), answer(p)])


def test_the_cap_draws_disagreements_by_stratum_with_their_chance_of_being_drawn() -> None:
    pairs = [pair(n) for n in range(10)] + [pair(n, "synthetic cases") for n in range(10, 14)]
    split = set(range(14)) - {0, 1}

    found = label_set.build(sample(pairs), answers(pairs, split), seed=7, cap=6, agreement=1)

    assert found.disagreement_strata == {
        "synthetic cases | effect_vs_none | trial_trial": 4,
        "synthetic symptoms | effect_vs_none | trial_trial": 8,
    }
    by = {s: [p for p in found.disagreement if p.stratum == s] for s in found.disagreement_strata}
    assert [len(v) for v in by.values()] == [2, 4]
    assert {p.inclusion for p in found.disagreement} == {0.5}
    assert len(found.agreement_order) == 2 and found.agreement_labeled == 1
    assert found.pair_ids == [p.pair_id for p in found.disagreement] + found.agreement_order[:1]


def test_the_draw_is_the_same_for_the_same_seed() -> None:
    pairs = [pair(n) for n in range(20)]
    split = set(range(0, 20, 2))

    first = label_set.build(sample(pairs), answers(pairs, split), seed=7, cap=4)
    again = label_set.build(sample(list(reversed(pairs))), answers(pairs, split), seed=7, cap=4)

    assert first == again


def test_an_arm_without_an_answer_is_refused() -> None:
    pairs = [pair(1), pair(2)]
    found = answers(pairs, set())
    del found[Arm.COUNCIL][pairs[1].pair_id]

    with pytest.raises(ValueError, match="council has no answer"):
        label_set.build(sample(pairs), found, seed=7)


def test_the_pairs_to_label_are_the_set_s_or_every_eval_pair(tmp_path: Path) -> None:
    pairs = [pair(n) for n in range(6)]
    drawn = sample(pairs)

    assert label_set.to_label(drawn, tmp_path) == pairs
    found = label_set.build(drawn, answers(pairs, {0, 1, 2}), seed=7, cap=2, agreement=1)
    label_set.write(tmp_path / label_set.LABEL_SET_FILE, found)

    chosen = label_set.to_label(drawn, tmp_path)
    assert len(chosen) == 3
    assert {p.pair_id for p in chosen} == set(found.pair_ids)
    assert label_set.read(tmp_path / label_set.LABEL_SET_FILE) == found
