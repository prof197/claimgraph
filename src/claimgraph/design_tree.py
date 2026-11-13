"""The design decision tree: questions about an abstract, asked in order, that settle its design.

A labeler answers the questions instead of picking a design by name, and the answers are kept in
the label, so every design traces back to what the abstract says. The wording is general, "the
exposure" and "the outcome"; the labeling guide spells each question out for its question.
"""

from collections.abc import Mapping
from typing import Self

from pydantic import model_validator

from claimgraph.models import Design, Record

FIRST = "subjects"


class Option(Record):
    value: str
    label: str
    # An answer either settles the design or leads to the next question.
    design: Design | None = None
    next: str | None = None

    @model_validator(mode="after")
    def _settles_or_continues(self) -> Self:
        if (self.design is None) == (self.next is None):
            raise ValueError("an option either settles a design or names the next question")
        return self


class Question(Record):
    key: str
    text: str
    options: list[Option]


def _yes_no(
    yes: Design | str, no: Design | str, yes_label: str = "Yes", no_label: str = "No"
) -> list[Option]:
    def option(value: str, label: str, to: Design | str) -> Option:
        if isinstance(to, Design):
            return Option(value=value, label=label, design=to)
        return Option(value=value, label=label, next=to)

    return [option("yes", yes_label, yes), option("no", no_label, no)]


QUESTIONS: tuple[Question, ...] = (
    Question(
        key="subjects",
        text="Is it a study on people?",
        options=[
            Option(value="people", label="People", next="source"),
            Option(value="animals", label="Animals", design=Design.ANIMAL),
            Option(value="cells_or_tissue", label="Cells or tissue", design=Design.IN_VITRO),
        ],
    ),
    Question(
        key="source",
        text="Does it report its own data, or summarize other studies?",
        options=[
            Option(value="own_data", label="Its own data", next="individual_patients"),
            Option(value="summary", label="A summary of other studies", next="systematic_methods"),
            Option(
                value="neither", label="Neither, such as a bibliometric study", design=Design.OTHER
            ),
        ],
    ),
    Question(
        key="systematic_methods",
        text=(
            "Does it say how it searched and how it chose studies, or call itself systematic, "
            "scoping, umbrella or a meta-analysis?"
        ),
        options=_yes_no("pools", Design.NARRATIVE_REVIEW),
    ),
    Question(
        key="pools",
        text="Does it pool results into one number?",
        options=_yes_no(Design.META_ANALYSIS, Design.SYSTEMATIC_REVIEW),
    ),
    Question(
        key="individual_patients",
        text="Does it describe one or a few patients individually?",
        options=_yes_no(Design.CASE_REPORT, "gave_exposure"),
    ),
    Question(
        key="gave_exposure",
        text=(
            "Did the researchers give the exposure, with results comparing the outcome by who got "
            "it, how much, or before and after?"
        ),
        options=_yes_no("concurrent_comparison", "proxy_or_event"),
    ),
    Question(
        key="concurrent_comparison",
        text="Is there a concurrent comparison group: placebo, none or another dose?",
        options=_yes_no("by_chance", Design.CASE_SERIES),
    ),
    Question(
        key="by_chance",
        text="Was who got what assigned by chance, to individuals or to clusters?",
        options=_yes_no(Design.RCT, Design.NON_RANDOMIZED_TRIAL),
    ),
    Question(
        key="proxy_or_event",
        text=(
            "Does the exposure come only from genetic proxies or an outside event, such as a "
            "fortification policy?"
        ),
        options=_yes_no(Design.OTHER, "selected_by_outcome"),
    ),
    Question(
        key="selected_by_outcome",
        text="Were participants picked because they had the outcome or did not?",
        options=_yes_no(Design.CASE_CONTROL, "measured_before"),
    ),
    Question(
        key="measured_before",
        text="Was the exposure measured before the outcome was assessed?",
        options=_yes_no(Design.COHORT, Design.CROSS_SECTIONAL),
    ),
)
_BY_KEY = {q.key: q for q in QUESTIONS}


def follow(answers: Mapping[str, str]) -> Design:
    """The design the answers lead to, stopping at the first result.

    Raises ValueError naming the first question left unanswered, an answer that is not one of
    its options, or answers to questions the path never reaches.
    """
    unknown = sorted(set(answers) - _BY_KEY.keys())
    if unknown:
        raise ValueError(f"unknown design questions: {', '.join(unknown)}")
    asked: list[str] = []
    key = FIRST
    while True:
        question = _BY_KEY[key]
        asked.append(key)
        value = answers.get(key)
        if value is None:
            raise ValueError(f"answer {key}: {question.text}")
        option = next((o for o in question.options if o.value == value), None)
        if option is None:
            values = ", ".join(o.value for o in question.options)
            raise ValueError(f"{key} must be one of {values}")
        if option.design is not None:
            stray = sorted(set(answers) - set(asked))
            if stray:
                raise ValueError(f"not asked once {key} is {value}: {', '.join(stray)}")
            return option.design
        assert option.next is not None
        key = option.next


def answers_for(design: Design) -> dict[str, str]:
    """The first path through the tree that settles this design, for examples and tests."""

    def walk(key: str, path: dict[str, str]) -> dict[str, str] | None:
        for option in _BY_KEY[key].options:
            here = path | {key: option.value}
            if option.design is design:
                return here
            if option.next is not None and (found := walk(option.next, here)):
                return found
        return None

    found = walk(FIRST, {})
    assert found is not None, design
    return found
