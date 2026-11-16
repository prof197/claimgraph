"""Hand-worked cases for the summary strip. Each expected number is worked in the comments."""

import pytest
from export_fixtures import REMISSION, STUDIES, SUMMARIES, SYMPTOMS

from claimgraph.compare import Direction
from claimgraph.export import DirectionShares, Weighting
from claimgraph.models import Design
from claimgraph.summary import HeadlineStudy, leading, summarize, weighted_view

# Written before the summary math; remove this marker once it is built.
pytestmark = pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="the summary math is not built yet"
)

B, N, H = Direction.BENEFIT, Direction.NONE, Direction.HARM


def study(key: str, direction: Direction) -> HeadlineStudy:
    design, size, _, _ = STUDIES[key]
    return HeadlineStudy(study=key, design=design, sample_size=size, direction=direction)


def made(design: Design, size: int | None, direction: Direction, n: int) -> HeadlineStudy:
    return HeadlineStudy(
        study=f"999000{n:03d}", design=design, sample_size=size, direction=direction
    )


def test_the_synthetic_symptoms_strip_works_out_by_hand() -> None:
    # Design: benefit 3 + 2 + 3 = 8, none 3, harm 1 + 0.5 = 1.5, of 12.5: benefit leads.
    # Unweighted: 3, 1 and 2 of 6: benefit leads. Sample size: benefit 5,960, none 18,000,
    # harm 658, of 24,618: no effect leads, so the size-aware line changes the picture.
    # 999000107 is of another design: shown, uncounted.
    studies = [
        study("999000101", B),
        study("999000102", N),
        study("999000103", B),
        study("999000104", B),
        study("999000105", H),
        study("999000106", H),
        study("999000107", N),
    ]

    assert summarize(studies, subgroup_only=1) == SUMMARIES[SYMPTOMS]


def test_the_synthetic_remission_strip_works_out_by_hand() -> None:
    # Design: benefit 6, none 9, of 15. Unweighted 2 and 3 of 5. Sample size 180 and 18,290.
    # No effect leads under all three, so nothing changes.
    studies = [
        study("999000101", B),
        study("999000102", N),
        study("999000104", N),
        study("999000110", B),
        study("999000111", N),
    ]

    assert summarize(studies, subgroup_only=0) == SUMMARIES[REMISSION]


def test_four_studies_are_thin_and_show_counts_only() -> None:
    studies = [made(Design.RCT, 100, d, i) for i, d in enumerate([B, B, N, H])]

    summary = summarize(studies, subgroup_only=0)

    assert summary.thin
    assert summary.views == []
    assert (summary.counts.benefit, summary.counts.none, summary.counts.harm) == (2, 1, 1)


def test_five_studies_are_not_thin() -> None:
    studies = [made(Design.RCT, 100, d, i) for i, d in enumerate([B, B, N, H, H])]

    summary = summarize(studies, subgroup_only=0)

    assert not summary.thin
    assert [v.weighting for v in summary.views] == list(Weighting)


def test_a_tie_has_no_leading_direction() -> None:
    # Design: benefit 3 + 3 = 6, none 3 + 3 = 6, harm 0.5: a tie. Unweighted 2, 2, 1: a tie,
    # which matches the design-weighted line. Sample size: benefit 200, none 400: no effect
    # leads, which changes the picture.
    studies = [
        made(Design.RCT, 100, B, 1),
        made(Design.RCT, 100, B, 2),
        made(Design.RCT, 100, N, 3),
        made(Design.RCT, 300, N, 4),
        made(Design.CASE_SERIES, 10, H, 5),
    ]

    views = {v.weighting: v for v in summarize(studies, subgroup_only=0).views}

    assert views[Weighting.DESIGN].leading is None
    assert views[Weighting.UNWEIGHTED].leading is None
    assert not views[Weighting.UNWEIGHTED].changes_leading
    assert views[Weighting.SAMPLE_SIZE].leading is N
    assert views[Weighting.SAMPLE_SIZE].changes_leading


def test_studies_without_a_sample_size_sit_out_the_size_aware_line() -> None:
    # Sample size counts only 999000002 and 999000003: none 50, harm 150, of 200.
    studies = [
        made(Design.RCT, None, B, 1),
        made(Design.RCT, 50, N, 2),
        made(Design.COHORT, 150, H, 3),
        made(Design.COHORT, None, B, 4),
        made(Design.CROSS_SECTIONAL, None, B, 5),
    ]

    view = weighted_view(studies, Weighting.SAMPLE_SIZE, reference=B)

    assert view is not None
    assert view.studies == 2
    assert view.shares == DirectionShares(benefit=0, none=50 / 200, harm=150 / 200)
    assert view.leading is H
    assert view.changes_leading


def test_with_no_sample_sizes_the_size_aware_line_is_left_out() -> None:
    studies = [made(Design.RCT, None, d, i) for i, d in enumerate([B, B, N, H, H])]

    assert weighted_view(studies, Weighting.SAMPLE_SIZE, reference=B) is None
    assert [v.weighting for v in summarize(studies, subgroup_only=0).views] == [
        Weighting.DESIGN,
        Weighting.UNWEIGHTED,
    ]


def test_a_design_without_a_weight_is_shown_but_not_counted() -> None:
    studies = [made(Design.RCT, 100, d, i) for i, d in enumerate([B, B, N, H, H])]
    other = made(Design.OTHER, 5000, N, 9)

    summary = summarize([*studies, other], subgroup_only=2)

    assert summary.studies == 5
    assert summary.uncounted == 1
    assert summary.subgroup_only == 2
    assert summary.counts.none == 1


def test_designs_run_most_first_then_in_the_weight_table_s_order() -> None:
    studies = [
        made(Design.CROSS_SECTIONAL, 10, B, 1),
        made(Design.COHORT, 10, B, 2),
        made(Design.RCT, 10, N, 3),
        made(Design.RCT, 10, H, 4),
        made(Design.CASE_SERIES, 10, B, 5),
    ]

    designs = [(d.design, d.studies) for d in summarize(studies, subgroup_only=0).designs]

    assert designs == [
        (Design.RCT, 2),
        (Design.COHORT, 1),
        (Design.CROSS_SECTIONAL, 1),
        (Design.CASE_SERIES, 1),
    ]


@pytest.mark.parametrize(
    ("shares", "expected"),
    [
        ({B: 0.2, N: 0.5, H: 0.3}, N),
        ({B: 0.5, N: 0.5, H: 0.0}, None),
        ({B: 1.0, N: 0.0, H: 0.0}, B),
    ],
)
def test_the_leading_direction_is_the_largest_share(
    shares: dict[Direction, float], expected: Direction | None
) -> None:
    assert leading(shares) is expected
