"""The summary strip's numbers for one outcome: studies, not findings, are the unit.

A study's direction on an outcome is its headline finding's, from the comparison: papers of one
trial are one study. Designs outside the weight table are shown but not counted, and reviews
are listed, never counted.
"""

import math
from collections import Counter
from collections.abc import Sequence

from claimgraph.compare import Direction
from claimgraph.export import (
    THIN_BELOW,
    DesignCount,
    DirectionCounts,
    DirectionShares,
    Summary,
    WeightedView,
    Weighting,
)
from claimgraph.models import DESIGN_WEIGHTS, Design, Record


class HeadlineStudy(Record):
    """One study's headline finding on an outcome, with what the weightings need."""

    study: str
    design: Design
    # Enrolled or randomized; None when the abstract does not say.
    sample_size: int | None
    direction: Direction


def leading(shares: dict[Direction, float]) -> Direction | None:
    """The direction with the largest share; None when the largest shares tie."""
    top = max(shares.values())
    first = [d for d, share in shares.items() if math.isclose(share, top, rel_tol=1e-9)]
    return first[0] if len(first) == 1 else None


def _weight(study: HeadlineStudy, weighting: Weighting) -> float | None:
    """The study's weight, or None when this weighting does not count it."""
    weight = DESIGN_WEIGHTS.get(study.design)
    if weight is None:
        return None
    match weighting:
        case Weighting.DESIGN:
            return weight
        case Weighting.UNWEIGHTED:
            return 1
        case Weighting.SAMPLE_SIZE:
            return None if study.sample_size is None else study.sample_size


def _shares(
    studies: Sequence[HeadlineStudy], weighting: Weighting
) -> tuple[int, dict[Direction, float]]:
    """How many studies the weighting counts, and each direction's share of their weight."""
    totals = dict.fromkeys(Direction, 0.0)
    counted = 0
    for study in studies:
        weight = _weight(study, weighting)
        if weight is not None:
            totals[study.direction] += weight
            counted += 1
    whole = sum(totals.values())
    if whole == 0:
        return 0, {}
    return counted, {d: total / whole for d, total in totals.items()}


def weighted_view(
    studies: Sequence[HeadlineStudy], weighting: Weighting, reference: Direction | None
) -> WeightedView | None:
    """Shares of the counted studies under one weighting; `reference` is the design-weighted
    leading direction. None when the weighting counts no study, as when no study reports a
    sample size."""
    counted, shares = _shares(studies, weighting)
    if not counted:
        return None
    lead = leading(shares)
    return WeightedView(
        weighting=weighting,
        studies=counted,
        shares=DirectionShares(
            benefit=shares[Direction.BENEFIT],
            none=shares[Direction.NONE],
            harm=shares[Direction.HARM],
        ),
        leading=lead,
        changes_leading=lead is not reference,
    )


def summarize(studies: Sequence[HeadlineStudy], subgroup_only: int) -> Summary:
    """The strip for one outcome from every study with a headline finding on it."""
    counted = [s for s in studies if s.design in DESIGN_WEIGHTS]
    directions = Counter(s.direction for s in counted)
    order = list(DESIGN_WEIGHTS)
    designs = Counter(s.design for s in counted)
    thin = len(counted) < THIN_BELOW
    views: list[WeightedView] = []
    if not thin:
        _, design_shares = _shares(counted, Weighting.DESIGN)
        reference = leading(design_shares)
        for weighting in Weighting:
            view = weighted_view(counted, weighting, reference)
            if view is not None:
                views.append(view)
    return Summary(
        studies=len(counted),
        counts=DirectionCounts(
            benefit=directions[Direction.BENEFIT],
            none=directions[Direction.NONE],
            harm=directions[Direction.HARM],
        ),
        designs=[
            DesignCount(design=d, studies=n)
            for d, n in sorted(designs.items(), key=lambda item: (-item[1], order.index(item[0])))
        ],
        thin=thin,
        views=views,
        subgroup_only=subgroup_only,
        uncounted=len(studies) - len(counted),
    )
