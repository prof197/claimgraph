"""Build the extraction model for one question: the fixed fields plus its custom dimensions."""

from typing import Any, Literal

from pydantic import Field, PositiveInt, create_model

from claimgraph.config import Dimension, DimensionType, QuestionConfig
from claimgraph.models import Design, FindingFields, Record


class ExtractionFields(Record):
    """Study-level fields the model fills once per abstract."""

    design: Design = Field(description="The study's design.")
    sample_size: PositiveInt | None = Field(
        description=(
            "Participants enrolled or randomized, not analyzed; null if the abstract does not say."
        )
    )
    review_summary: str | None = Field(
        description=(
            "For a systematic review or meta-analysis only: one sentence summarizing its "
            "conclusion. Null for any other design."
        )
    )


def _field_type(dimension: Dimension) -> Any:
    # Every dimension is nullable but required, so the model must answer each one,
    # even if only to say the abstract doesn't report it.
    match dimension.type:
        case DimensionType.TEXT:
            return str | None
        case DimensionType.OPTIONS:
            options: Any = Literal[tuple(dimension.options or ())]
            return options | None
        case DimensionType.NUMBER:
            return float | None


def _description(dimension: Dimension) -> str:
    if dimension.unit is None:
        return dimension.definition
    return f"{dimension.definition} Unit: {dimension.unit}."


def build_finding_model(config: QuestionConfig) -> type[FindingFields]:
    fields: dict[str, Any] = {
        d.name: (_field_type(d), Field(description=_description(d))) for d in config.dimensions
    }
    return create_model("ExtractedFinding", __base__=FindingFields, **fields)


def build_extraction_model(config: QuestionConfig) -> type[ExtractionFields]:
    finding_model = build_finding_model(config)
    findings = (
        list[finding_model],
        Field(
            description=(
                "Every finding the abstract reports, one per outcome, arm or subgroup. "
                "Empty for a systematic review or meta-analysis."
            )
        ),
    )
    return create_model("Extraction", __base__=ExtractionFields, findings=findings)
