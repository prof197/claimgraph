"""The drawn contradiction pairs: the eval's pairs and the dev pairs, in one committed file.

A drawn pair names the two counted findings it compares, so a labeling page or a classifier
reads both findings' fields from the extraction without redoing the comparison.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import model_validator

from claimgraph.models import (
    REVIEW_DESIGNS,
    CandidatePair,
    Design,
    FindingId,
    NonBlank,
    Record,
)

SAMPLE_FILE = "contradiction_sample.json"

TRIALS = frozenset({Design.RCT, Design.NON_RANDOMIZED_TRIAL})
# Case series count as observational; other primary designs carry no design weight.
OBSERVATIONAL = frozenset(
    {Design.COHORT, Design.CASE_CONTROL, Design.CROSS_SECTIONAL, Design.CASE_SERIES}
)


class Combination(StrEnum):
    """The two studies' designs, a stratum of the draw."""

    TRIAL_TRIAL = "trial_trial"
    TRIAL_OBSERVATIONAL = "trial_observational"
    OBSERVATIONAL_OBSERVATIONAL = "observational_observational"
    WITH_OTHER = "with_other"


def combination(a: Design, b: Design) -> Combination:
    if a in REVIEW_DESIGNS or b in REVIEW_DESIGNS:
        raise ValueError("reviews are never paired")
    if a not in TRIALS | OBSERVATIONAL or b not in TRIALS | OBSERVATIONAL:
        return Combination.WITH_OTHER
    trials = (a in TRIALS) + (b in TRIALS)
    return [
        Combination.OBSERVATIONAL_OBSERVATIONAL,
        Combination.TRIAL_OBSERVATIONAL,
        Combination.TRIAL_TRIAL,
    ][trials]


class DrawnPair(CandidatePair):
    combination: Combination
    # The counted findings of study a and study b on the outcome.
    finding_a: FindingId
    finding_b: FindingId


class PairSample(Record):
    question: NonBlank
    seed: int
    # Labeled, classified by every arm and replayed in CI. Both studies openly licensed.
    eval: list[DrawnPair]
    # For tuning the classification prompt only; no study of a dev pair is in an eval pair.
    dev: list[DrawnPair]

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        ids = [p.pair_id for p in self.eval + self.dev]
        if len(set(ids)) != len(ids):
            raise ValueError("a pair is drawn once")
        studies = {s for p in self.eval for s in (p.study_a, p.study_b)}
        if any(s in studies for p in self.dev for s in (p.study_a, p.study_b)):
            raise ValueError("dev pairs share no study with eval pairs")
        return self


def read_sample(path: Path) -> PairSample:
    return PairSample.model_validate_json(path.read_text())
