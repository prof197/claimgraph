import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from claimgraph import pair_sample
from claimgraph.models import Design, PairKind
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
