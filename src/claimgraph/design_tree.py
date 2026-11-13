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

# How to read the harder questions, in the same general terms. The labeling guide gives the same
# readings in a question's own words, so labels and the model answer alike.
READINGS: dict[str, str] = {
    "systematic_methods": (
        "Saying how it searched means more than naming the databases; saying how it chose "
        "studies means eligibility criteria, screening, or a count of included studies. A "
        "review that says it followed PRISMA answers yes. A review titled narrative that says "
        "both is systematic. Guidelines, position statements and perspectives that review "
        "others' results are summaries, so this question decides them too."
    ),
    "gave_exposure": (
        "An exposure the participants chose to take themselves was not given by the "
        "researchers. A follow-up analysis of trial participants that does not compare the "
        "randomized arms answers no."
    ),
    "concurrent_comparison": (
        "A single-arm study, where everyone gets the exposure and outcomes are compared before "
        "and after, answers no, as does a comparison with historical controls only, an earlier "
        "group that did not get the exposure. Both are case series, even when the abstract calls "
        "them a trial. A comparison group got none of the exposure or a different amount; a "
        "group that differs only by outcome status, such as healthy controls beside treated "
        "patients, is not one."
    ),
    "by_chance": "Allocation by alternation, date of birth or clinic is not by chance.",
    "proxy_or_event": "Mendelian randomization and natural experiments answer yes.",
    "selected_by_outcome": (
        "Yes when the study picked people with the outcome and people without it to compare; a "
        "sample of people with the outcome alone answers no."
    ),
    "measured_before": (
        "A study that measures the exposure and the outcome at the same visit answers no, even "
        "inside a cohort study that reports only that visit."
    ),
}


def _walk(answers: Mapping[str, str]) -> tuple[Design, list[str]]:
    """The design the answers lead to from the first question, and the questions asked."""
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
            return option.design, asked
        assert option.next is not None
        key = option.next


def follow(answers: Mapping[str, str]) -> Design:
    """The design a labeler's answers lead to, stopping at the first result.

    Raises ValueError naming the first question left unanswered, an answer that is not one of
    its options, or answers to questions the path never reaches.
    """
    unknown = sorted(set(answers) - _BY_KEY.keys())
    if unknown:
        raise ValueError(f"unknown design questions: {', '.join(unknown)}")
    design, asked = _walk(answers)
    stray = sorted(set(answers) - set(asked))
    if stray:
        raise ValueError(f"not asked once {asked[-1]} is {answers[asked[-1]]}: {', '.join(stray)}")
    return design


def settle(answers: Mapping[str, str]) -> Design:
    """The design a full set of answers leads to, as the extraction model gives them.

    The model answers every question on the abstract's own facts, and all the path logic is
    here: the tree is walked from the first question, and answers off the path are ignored.
    """
    design, _ = _walk(answers)
    return design


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


def full_answers(design: Design) -> dict[str, str]:
    """Every question answered, settling this design: its path, then "no" or the first option
    for the questions off it. For examples and tests."""
    off_path = {
        q.key: "no" if any(o.value == "no" for o in q.options) else q.options[0].value
        for q in QUESTIONS
    }
    return off_path | answers_for(design)
