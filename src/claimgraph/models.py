"""Records passed between pipeline stages. Each stage file is JSON Lines of one of these."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    model_validator,
)

Pmid = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
# A PubMed Central accession, such as PMC8913152.
Pmcid = Annotated[str, StringConstraints(pattern=r"^PMC[1-9][0-9]*$")]
FindingId = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*-[1-9][0-9]*$")]
# A trial registration as REGISTRY:ACCESSION, upper-cased, such as CLINICALTRIALS.GOV:NCT01169259.
TrialId = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9. -]*:[^\sa-z]+$")]
NonBlank = Annotated[str, StringConstraints(pattern=r"\S")]
DimensionValue = str | float | None


class Design(StrEnum):
    RCT = "rct"
    NON_RANDOMIZED_TRIAL = "non_randomized_trial"
    COHORT = "cohort"
    CASE_CONTROL = "case_control"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_SERIES = "case_series"
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"
    # A review without systematic methods. OTHER holds only primary studies outside the
    # weighted designs, so how often the model uses it remains a clean signal.
    NARRATIVE_REVIEW = "narrative_review"
    CASE_REPORT = "case_report"
    ANIMAL = "animal"
    IN_VITRO = "in_vitro"
    OTHER = "other"


class Effect(StrEnum):
    """What happened to the measure as reported, before polarity is applied."""

    INCREASED = "increased"
    DECREASED = "decreased"
    NO_CHANGE = "no_change"


class Direction(StrEnum):
    """Effect combined with the reviewed polarity of the outcome."""

    BENEFIT = "benefit"
    NO_EFFECT = "no_effect"
    HARM = "harm"


class Reason(StrEnum):
    """Why two studies disagree: Cochrane's clinical and methodological heterogeneity."""

    POPULATION = "population"
    INTERVENTION = "intervention"
    COMPARATOR = "comparator"
    OUTCOME_MEASURE = "outcome_measure"
    DESIGN_OR_BIAS = "design_or_bias"
    UNCLEAR = "unclear"


class Verdict(StrEnum):
    GENUINE = "genuine"
    APPARENT = "apparent"


class PairKind(StrEnum):
    EFFECT_VS_NONE = "effect_vs_none"
    BENEFIT_VS_HARM = "benefit_vs_harm"


class Arm(StrEnum):
    """Classification arms; the values double as `--depth` names."""

    SINGLE_SMALL = "single-small"
    SINGLE_MID = "single-mid"
    SELF_CONSISTENCY = "self-consistency"
    SELF_MOA = "self-moa"
    COUNCIL = "council"
    COUNCIL_RANKED = "council-ranked"


# Weights for the summary strip. Designs absent from this table are not counted:
# reviews are summarized, not counted, OTHER is shown without a weight, and the default
# exclusions are dropped unless a question config keeps them.
DESIGN_WEIGHTS: dict[Design, float] = {
    Design.RCT: 3,
    Design.NON_RANDOMIZED_TRIAL: 2,
    Design.COHORT: 2,
    Design.CASE_CONTROL: 1,
    Design.CROSS_SECTIONAL: 1,
    Design.CASE_SERIES: 0.5,
}
REVIEW_DESIGNS = frozenset(
    {Design.SYSTEMATIC_REVIEW, Design.META_ANALYSIS, Design.NARRATIVE_REVIEW}
)
DEFAULT_EXCLUDED_DESIGNS = frozenset({Design.CASE_REPORT, Design.ANIMAL, Design.IN_VITRO})


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Study(Record):
    pmid: Pmid
    title: str
    first_author: str | None
    journal: str
    trial_ids: list[TrialId]
    year: int
    language: str
    design: Design
    # Enrolled or randomized, not analyzed.
    sample_size: PositiveInt | None

    @property
    def label(self) -> str:
        if self.first_author is None:
            return f"PMID {self.pmid}"
        return f"{self.first_author} {self.year}"


class FindingFields(Record):
    """The fixed fields of a finding. The descriptions are shown to the extraction model."""

    claim: NonBlank = Field(description="One plain-language sentence stating the finding.")
    # Kept byte for byte: it is checked against the abstract text.
    quote: NonBlank = Field(
        description="The sentence from the abstract that reports this finding, copied exactly."
    )
    outcome_reported: NonBlank = Field(
        description="The outcome measure as the abstract names it, such as 'PHQ-9 score'."
    )
    effect: Effect = Field(
        description=(
            "What happened to the measure with the intervention compared with the comparator: "
            "increased, decreased, or no_change when the abstract reports no significant "
            "difference."
        )
    )
    is_primary: bool = Field(
        description="True if the abstract names this as the study's primary outcome."
    )
    population: str = Field(description="Who was enrolled, as reported.")
    intervention: str = Field(description="Agent, dose, duration, route and form, as reported.")
    comparator: str = Field(description="Placebo, active treatment or usual care, as reported.")


class Finding(FindingFields):
    finding_id: FindingId
    dimensions: dict[str, DimensionValue]

    @property
    def pmid(self) -> str:
        return self.finding_id.rsplit("-", 1)[0]


class NormalizedOutcome(Record):
    name: NonBlank
    members: Annotated[list[NonBlank], Field(min_length=1)]
    higher_is_better: bool
    reviewed: bool

    @model_validator(mode="after")
    def _members_are_unique(self) -> Self:
        if len(set(self.members)) != len(self.members):
            raise ValueError("member strings must be unique")
        return self


class CandidatePair(Record):
    outcome: NonBlank
    study_a: NonBlank
    study_b: NonBlank
    kind: PairKind

    @model_validator(mode="after")
    def _studies_in_canonical_order(self) -> Self:
        # One order per pair keeps pair_id stable across runs and arms.
        if not self.study_a < self.study_b:
            raise ValueError("study_a must sort strictly before study_b")
        return self

    @property
    def pair_id(self) -> str:
        return f"{self.outcome}|{self.study_a}|{self.study_b}"


class Classification(Record):
    pair_id: NonBlank
    verdict: Verdict
    reason: Reason
    also_differs: list[Reason]
    detail: NonBlank
    rationale: NonBlank
    arm: Arm

    @model_validator(mode="after")
    def _also_differs_adds_new_reasons(self) -> Self:
        check_also_differs(self.reason, self.also_differs)
        return self


def check_also_differs(reason: Reason, also_differs: list[Reason]) -> None:
    """Secondary reasons add differences beyond the primary one; shared with the gold labels."""
    if reason in also_differs:
        raise ValueError("also_differs must not repeat the primary reason")
    if len(set(also_differs)) != len(also_differs):
        raise ValueError("also_differs must not repeat a reason")
    if Reason.UNCLEAR in also_differs:
        raise ValueError("unclear is not a difference")
