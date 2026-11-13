"""Build the extraction model for one question: the fixed fields plus its custom dimensions."""

from typing import Any, Literal

from pydantic import Field, PositiveInt, create_model

from claimgraph.config import OUTSIDE_DOMAIN, Dimension, DimensionType, QuestionConfig
from claimgraph.design_tree import QUESTIONS
from claimgraph.models import FindingFacts, FindingFields, Record


def _answer_type(values: list[str]) -> Any:
    answer: Any = Literal[tuple(values)]
    return answer


# The model answers every design question on the abstract's own facts rather than naming a
# design, and code walks the tree, so a wrong design traces back to the question that went wrong.
_ANSWER_FIELDS: dict[str, Any] = {
    q.key: (_answer_type([o.value for o in q.options]), Field(description=q.text))
    for q in QUESTIONS
}
DesignAnswers: type[Record] = create_model("DesignAnswers", __base__=Record, **_ANSWER_FIELDS)


class ExtractionFields(Record):
    """Study-level fields the model fills once per abstract."""

    design_answers: DesignAnswers = Field(  # pyright: ignore[reportInvalidTypeForm]
        description=(
            "Every design question, each answered on the abstract's own facts; a question that "
            "does not fit the study is answered no."
        )
    )
    sample_size: PositiveInt | None = Field(
        description=(
            "Participants enrolled or randomized, not analyzed; null if the abstract does not say."
        )
    )
    review_summary: str | None = Field(
        description=(
            "For a review only, systematic, meta-analysis or narrative: one sentence "
            "summarizing its conclusion. Null for any other design."
        )
    )
    # Counted in the run manifest as out of scope; never scored or stored as findings.
    other_outcomes: list[str] = Field(
        description=(
            "Outcomes outside the outcome domain that the abstract reports results for, named as "
            "the abstract names them. Empty if there are none."
        )
    )


def _field_type(dimension: Dimension) -> Any:
    # Every dimension is required, so the model must answer each one. Text and number
    # dimensions answer "not reported" with null; options dimensions with their
    # not_reported option, so they have exactly one absent value.
    match dimension.type:
        case DimensionType.TEXT:
            return str | None
        case DimensionType.OPTIONS:
            options: Any = Literal[tuple(dimension.options or ())]
            return options
        case DimensionType.NUMBER:
            return float | None


def _description(dimension: Dimension) -> str:
    if dimension.unit is None:
        return dimension.definition
    return f"{dimension.definition} Unit: {dimension.unit}."


def _fixed_fields() -> dict[str, Any]:
    # The model reports the facts the effect is worked out from, where the effect would sit,
    # rather than the effect itself.
    fields: dict[str, Any] = {}
    for name, info in FindingFields.model_fields.items():
        if name == "effect":
            fields |= {n: (f.annotation, f) for n, f in FindingFacts.model_fields.items()}
        else:
            fields[name] = (info.annotation, info)
    return fields


def build_finding_model(config: QuestionConfig) -> type[Record]:
    # The model names the domain entry each finding is on, with an explicit way out, so a
    # finding on another outcome is marked and dropped rather than filed under a domain entry.
    domain: Any = Literal[tuple([*config.outcome_domain, OUTSIDE_DOMAIN])]
    fields: dict[str, Any] = (
        _fixed_fields()
        | {
            "domain_outcome": (
                domain,
                Field(
                    description=(
                        "The entry of the outcome domain this finding is on, or "
                        f"'{OUTSIDE_DOMAIN}' when its outcome fits none."
                    )
                ),
            )
        }
        | {d.name: (_field_type(d), Field(description=_description(d))) for d in config.dimensions}
    )
    return create_model("ExtractedFinding", __base__=Record, **fields)


def build_extraction_model(config: QuestionConfig) -> type[ExtractionFields]:
    finding_model = build_finding_model(config)
    findings = (
        list[finding_model],
        Field(
            description=(
                "Every finding the abstract reports, one per outcome, arm or subgroup. "
                "Empty for a review."
            )
        ),
    )
    return create_model("Extraction", __base__=ExtractionFields, findings=findings)
