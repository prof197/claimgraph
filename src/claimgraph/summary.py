"""The summary strip's numbers for one outcome: studies, not findings, are the unit.

A study's direction on an outcome is its headline finding's, from the comparison: papers of one
trial are one study. Designs outside the weight table are shown but not counted, and reviews
are listed, never counted.
"""

from collections.abc import Sequence

from claimgraph.compare import Direction
from claimgraph.export import Summary, WeightedView, Weighting
from claimgraph.models import Design, Record


class HeadlineStudy(Record):
    """One study's headline finding on an outcome, with what the weightings need."""

    study: str
    design: Design
    # Enrolled or randomized; None when the abstract does not say.
    sample_size: int | None
    direction: Direction


def leading(shares: dict[Direction, float]) -> Direction | None:
    """The direction with the largest share; None when the largest shares tie."""
    raise NotImplementedError


def weighted_view(
    studies: Sequence[HeadlineStudy], weighting: Weighting, reference: Direction | None
) -> WeightedView | None:
    """Shares of the counted studies under one weighting; `reference` is the design-weighted
    leading direction. None when the weighting counts no study, as when no study reports a
    sample size."""
    raise NotImplementedError


def summarize(studies: Sequence[HeadlineStudy], subgroup_only: int) -> Summary:
    """The strip for one outcome from every study with a headline finding on it."""
    raise NotImplementedError
