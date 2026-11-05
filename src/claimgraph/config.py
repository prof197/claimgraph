"""Question configs: the committed, reproducible definition of one research question."""

import tomllib
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from claimgraph.models import DEFAULT_EXCLUDED_DESIGNS, Design, Finding, NonBlank, Reason, Record

MAX_DIMENSIONS = 5
# The only absent value for an options dimension; text and number dimensions use null.
NOT_REPORTED = "not_reported"

QuestionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
DimensionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class DimensionType(StrEnum):
    TEXT = "text"
    OPTIONS = "options"
    NUMBER = "number"


class Dimension(Record):
    """An expert-defined field that refines one of the fixed contradiction reasons."""

    name: DimensionName
    definition: NonBlank
    type: DimensionType
    refines: Reason
    options: list[NonBlank] | None = None
    unit: NonBlank | None = None

    @model_validator(mode="after")
    def _fits_its_type(self) -> Self:
        if self.refines is Reason.UNCLEAR:
            raise ValueError("a dimension must refine a specific reason, not unclear")
        if self.type is DimensionType.OPTIONS:
            if not self.options:
                raise ValueError("an options dimension needs a non-empty options list")
            if len(set(self.options)) != len(self.options):
                raise ValueError("options must be unique")
            if NOT_REPORTED not in self.options:
                raise ValueError(f"an options dimension must include {NOT_REPORTED!r}")
        elif self.options is not None:
            raise ValueError("only an options dimension takes options")
        if self.type is DimensionType.NUMBER:
            if self.unit is None:
                raise ValueError("a number dimension needs a unit")
        elif self.unit is not None:
            raise ValueError("only a number dimension takes a unit")
        return self


class QuestionConfig(Record):
    id: QuestionId
    title: NonBlank
    query: NonBlank
    # PubMed-syntax clauses joined to the query with AND.
    filters: list[NonBlank] = Field(default_factory=list)
    start_date: date | None = None
    # A fixed end date keeps reruns from drifting as PubMed grows.
    end_date: date
    exclude_designs: frozenset[Design] = DEFAULT_EXCLUDED_DESIGNS
    dimensions: list[Dimension] = Field(default_factory=list[Dimension], max_length=MAX_DIMENSIONS)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.start_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        names = [d.name for d in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError("dimension names must be unique")
        clashes = sorted(set(names) & Finding.model_fields.keys())
        if clashes:
            raise ValueError(f"dimension names clash with finding fields: {', '.join(clashes)}")
        return self


def load_config(path: Path) -> QuestionConfig:
    with path.open("rb") as f:
        config = QuestionConfig.model_validate(tomllib.load(f))
    if config.id != path.stem:
        raise ValueError(f"config id {config.id!r} does not match file name {path.name!r}")
    return config
