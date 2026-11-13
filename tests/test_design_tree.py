"""The design decision tree: every design is reachable, and answers are checked as a path."""

import pytest

from claimgraph.design_tree import FIRST, QUESTIONS, answers_for, follow
from claimgraph.models import Design


def test_every_design_has_exactly_one_path() -> None:
    reached: list[Design] = []

    def walk(key: str) -> None:
        for option in next(q for q in QUESTIONS if q.key == key).options:
            if option.design is not None:
                reached.append(option.design)
            else:
                assert option.next is not None
                walk(option.next)

    walk(FIRST)

    # Other is reached two ways: neither own data nor a summary, or a proxy or outside exposure.
    assert sorted(reached) == sorted([*Design, Design.OTHER])


def test_questions_are_unique_and_all_asked() -> None:
    keys = [q.key for q in QUESTIONS]
    targets = {o.next for q in QUESTIONS for o in q.options if o.next is not None}

    assert len(set(keys)) == len(keys)
    assert targets | {FIRST} == set(keys)


@pytest.mark.parametrize(
    ("answers", "design"),
    [
        pytest.param({"subjects": "animals"}, Design.ANIMAL, id="animals"),
        pytest.param(
            {"subjects": "people", "source": "summary", "systematic_methods": "no"},
            Design.NARRATIVE_REVIEW,
            id="narrative review",
        ),
        pytest.param(
            {
                "subjects": "people",
                "source": "summary",
                "systematic_methods": "yes",
                "pools": "yes",
            },
            Design.META_ANALYSIS,
            id="meta-analysis",
        ),
        pytest.param({"subjects": "people", "source": "neither"}, Design.OTHER, id="bibliometric"),
        pytest.param(
            {
                "subjects": "people",
                "source": "own_data",
                "individual_patients": "no",
                "gave_exposure": "yes",
                "concurrent_comparison": "no",
            },
            Design.CASE_SERIES,
            id="single arm",
        ),
        pytest.param(
            {
                "subjects": "people",
                "source": "own_data",
                "individual_patients": "no",
                "gave_exposure": "no",
                "proxy_or_event": "no",
                "selected_by_outcome": "no",
                "measured_before": "yes",
            },
            Design.COHORT,
            id="cohort",
        ),
    ],
)
def test_answers_lead_to_a_design(answers: dict[str, str], design: Design) -> None:
    assert follow(answers) is design


@pytest.mark.parametrize("design", list(Design))
def test_answers_for_each_design_lead_back_to_it(design: Design) -> None:
    assert follow(answers_for(design)) is design


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        pytest.param({}, "answer subjects", id="nothing answered"),
        pytest.param({"subjects": "people"}, "answer source", id="stops short"),
        pytest.param({"subjects": "plants"}, "subjects must be one of", id="unknown option"),
        pytest.param({"subjects": "animals", "colour": "teal"}, "unknown", id="unknown question"),
        pytest.param(
            {"subjects": "animals", "source": "summary"}, "not asked", id="answer off the path"
        ),
    ],
)
def test_incomplete_or_stray_answers_are_named(answers: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        follow(answers)
