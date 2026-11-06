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
# A word or phrase searched as typed; a trailing * matches any ending, as PubMed truncation does.
# Quotes and field tags are added by the loader, so a term holds neither.
TopicTerm = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r'^[^"\[\]*\s][^"\[\]*]*\*?$')
]
TopicTerms = Annotated[list[TopicTerm], Field(min_length=1)]
# A MeSH heading as NLM spells it, searched with [mh], which also matches the narrower headings
# beneath it in the MeSH tree.
MeshHeading = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r'^[^"\[\]*\s][^"\[\]*]*$')
]


class TopicMatch(StrEnum):
    """Where both topics must appear: the title alone, or the title or abstract."""

    TITLE = "title"
    TITLE_ABSTRACT = "title_abstract"

    @property
    def field_tag(self) -> str:
        return "ti" if self is TopicMatch.TITLE else "tiab"


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
    # The topic query is built from these: any exposure term AND any outcome term, each matched
    # in the fields topic_match names.
    exposure_terms: TopicTerms
    outcome_terms: TopicTerms
    topic_match: TopicMatch = TopicMatch.TITLE_ABSTRACT
    # Indexers assign MeSH headings from the full text, so these find records whose abstract
    # words miss the topic. They join the terms in title_abstract scope only; title scope stays
    # a test of the title alone.
    exposure_mesh: list[MeshHeading] = Field(default_factory=list[MeshHeading])
    outcome_mesh: list[MeshHeading] = Field(default_factory=list[MeshHeading])
    # The outcomes this question is about. Extraction keeps findings on these and counts the
    # rest as out of scope, so a broad search does not fill the map with unrelated outcomes.
    outcome_domain: Annotated[list[NonBlank], Field(min_length=1)]
    # PubMed-syntax clauses joined to the topic query with AND.
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
        for terms in (self.exposure_terms, self.outcome_terms):
            if len(set(terms)) != len(terms):
                raise ValueError("topic terms must be unique")
        for headings in (self.exposure_mesh, self.outcome_mesh):
            if len(set(headings)) != len(headings):
                raise ValueError("MeSH headings must be unique")
        if len(set(self.outcome_domain)) != len(self.outcome_domain):
            raise ValueError("outcome domain entries must be unique")
        names = [d.name for d in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError("dimension names must be unique")
        clashes = sorted(set(names) & Finding.model_fields.keys())
        if clashes:
            raise ValueError(f"dimension names clash with finding fields: {', '.join(clashes)}")
        return self

    def topic_query(self, match: TopicMatch | None = None) -> str:
        match = match or self.topic_match
        tag = match.field_tag

        def any_of(terms: list[str], headings: list[str]) -> str:
            # Truncated terms cannot be quoted in PubMed; whole terms are quoted as phrases.
            clauses = [
                f"{term}[{tag}]" if term.endswith("*") else f'"{term}"[{tag}]' for term in terms
            ]
            if match is TopicMatch.TITLE_ABSTRACT:
                clauses += [f'"{heading}"[mh]' for heading in headings]
            return " OR ".join(clauses)

        exposure = any_of(self.exposure_terms, self.exposure_mesh)
        outcome = any_of(self.outcome_terms, self.outcome_mesh)
        return f"({exposure}) AND ({outcome})"


def load_config(path: Path) -> QuestionConfig:
    with path.open("rb") as f:
        config = QuestionConfig.model_validate(tomllib.load(f))
    if config.id != path.stem:
        raise ValueError(f"config id {config.id!r} does not match file name {path.name!r}")
    return config


def resolve_question(question: str, questions_dir: Path = Path("questions")) -> Path:
    """A QUESTION argument is a path to a config, or the id of one in questions/."""
    path = Path(question)
    if path.suffix == ".toml":
        return path
    return questions_dir / f"{question}.toml"
