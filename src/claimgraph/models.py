"""Records passed between pipeline stages. Each stage file is JSON Lines of one of these."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
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


class ExposureSide(StrEnum):
    """Which way the abstract's own wording of a result points on the exposure."""

    MORE = "more"
    LESS = "less"


_FLIPPED = {Effect.INCREASED: Effect.DECREASED, Effect.DECREASED: Effect.INCREASED}


def effect_from(side: ExposureSide, direction: Effect) -> Effect:
    """The effect on the side with more of the exposure, from a result worded either way round.

    "Deficiency was linked to more of the outcome" is less of the exposure and the outcome up,
    so the effect is decreased. The model reports the two facts and code turns them round, so a
    result stated from deficiency cannot flip and make a false contradiction.
    """
    if side is ExposureSide.MORE:
        return direction
    return _FLIPPED.get(direction, direction)


class Comparison(StrEnum):
    """What a result compares. A trial's findings are its comparisons between groups."""

    # Named so that a continuous association across participants plainly fits it.
    BETWEEN = "between_groups_or_across_participants"
    WITHIN = "within_one_group"
    POOLED = "pooled_across_groups"


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
    # One finding's exposure is not the question's, so the pair compares nothing on the
    # question: a finding extraction should have dropped. It takes no reason, is left out of the
    # panel and the eval, and is counted.
    OFF_QUESTION = "off_question"


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
        description=(
            "The sentence from the abstract that reports this finding, copied exactly: a Results "
            "sentence, or a Conclusion sentence only when no Results sentence reports it."
        )
    )
    outcome_reported: NonBlank = Field(
        description=(
            "The outcome measure as the abstract names it, including the instrument when the "
            "abstract names one, such as 'symptom severity (PHQ-9)' or 'PHQ-9 score'."
        )
    )
    # Worked out by effect_from from the facts the model reports, never asked for directly.
    effect: Effect = Field(
        description=(
            "What happened to the measure on the side with more of the exposure: increased, "
            "decreased, or no_change when the abstract reports no significant difference."
        )
    )
    is_primary: bool = Field(
        description="True if the abstract names this as the study's primary outcome."
    )
    # A subgroup result never stands for its study on an outcome, so a study with only
    # subgroup results there is shown but not counted or paired.
    is_subgroup: bool = Field(
        description=(
            "True if the result covers only part of the analyzed sample, split by a "
            "characteristic such as sex, age or baseline exposure. A study that enrolled only "
            "one group reports that group as its population, not as a subgroup."
        )
    )
    # One sentence can report several subgroups, so the name tells their findings apart.
    subgroup: str | None = Field(
        description=(
            "For a subgroup result, which part of the sample it covers, as the abstract names "
            "it. Null for a result on the whole analyzed sample."
        )
    )
    population: str = Field(description="Who was enrolled, as reported.")
    intervention: str = Field(description="Agent, dose, duration, route and form, as reported.")
    comparator: str = Field(description="Placebo, active treatment or usual care, as reported.")


class FindingFacts(Record):
    """Facts the model reports for each finding in place of its effect, kept as provenance."""

    exposure_side: ExposureSide = Field(
        description=(
            "Which side of the result the abstract's sentence names: more when it is worded "
            "from more of the exposure, a higher level, intake or dose, or getting it rather "
            "than placebo; less when it is worded from less, such as deficiency, low levels, "
            "the lowest group or placebo."
        )
    )
    outcome_direction: Effect = Field(
        description=(
            "What happened to the outcome on that named side: increased, decreased, or "
            "no_change when the abstract reports no significant difference."
        )
    )
    comparison: Comparison = Field(
        description=(
            "What the result compares: between_groups_or_across_participants for groups "
            "compared with each other or an association across participants; "
            "within_one_group for a change over time inside one group; pooled_across_groups "
            "for a change over time with all groups taken together."
        )
    )


class Finding(FindingFields, FindingFacts):
    finding_id: FindingId
    # Which entry of the question's outcome domain the finding is on, as the model named it.
    domain_outcome: NonBlank
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


class PairClassification(Record):
    """What a classifier said about one candidate pair."""

    pair_id: NonBlank
    verdict: Verdict
    # None exactly when the verdict is off_question.
    reason: Reason | None
    also_differs: list[Reason]
    detail: NonBlank
    rationale: NonBlank

    @model_validator(mode="after")
    def _reasons_fit_the_verdict(self) -> Self:
        check_reasons(self.verdict, self.reason, self.also_differs)
        return self


class Classification(PairClassification):
    arm: Arm


class DirectionWords(Record):
    """What a study's badge says for each direction it found."""

    benefit: NonBlank
    none: NonBlank
    harm: NonBlank


class FoundWords(Record):
    """A study's badge, worded by design: a trial gave the exposure and found a difference or
    not; an observational study found a link, which is not a benefit. Each question words its
    own, so its outcome can be named."""

    trial: DirectionWords
    observational: DirectionWords


# For a question that words none of its own.
DEFAULT_FOUND = FoundWords(
    trial=DirectionWords(benefit="Found a benefit", none="No clear difference", harm="Found harm"),
    observational=DirectionWords(
        benefit="Linked to benefit", none="No clear link", harm="Linked to harm"
    ),
)


class PairSelection(Record):
    """How the classified pairs were chosen from the candidates: those whose two studies are
    alike in at least `minimum` of `fields` ways, both reporting each, with sample sizes alike
    within `size_ratio`, and no study in more than `per_study` pairs."""

    minimum: PositiveInt
    fields: PositiveInt
    size_ratio: PositiveInt
    per_study: PositiveInt | None
    # Chosen pairs with no recorded answer yet, left unclassified by a replay-only run.
    unclassified: NonNegativeInt = 0


class ClassifiedPairs(Record):
    """A run's classified pairs, the file the export reads its contradictions from."""

    # The arm that classified every pair. None marks stub classifications, made without a
    # model so the page can be built before classification runs; they are never hosted.
    depth: Arm | None
    # Candidate pairs in the run, classified or not.
    candidates: NonNegativeInt
    # None when every candidate pair was sent.
    selection: PairSelection | None = None
    classifications: list[PairClassification]

    @model_validator(mode="after")
    def _one_per_pair(self) -> Self:
        ids = [c.pair_id for c in self.classifications]
        if len(set(ids)) != len(ids):
            raise ValueError("a pair is classified once")
        if len(ids) > self.candidates:
            raise ValueError("more pairs classified than there are candidates")
        return self


def check_also_differs(reason: Reason, also_differs: list[Reason]) -> None:
    """Secondary reasons add differences beyond the primary one; shared with the gold labels."""
    if reason in also_differs:
        raise ValueError("also_differs must not repeat the primary reason")
    if len(set(also_differs)) != len(also_differs):
        raise ValueError("also_differs must not repeat a reason")
    if Reason.UNCLEAR in also_differs:
        raise ValueError("unclear is not a difference")


def check_reasons(verdict: Verdict, reason: Reason | None, also_differs: list[Reason]) -> None:
    """A pair's reasons against its verdict; shared by the classifier and the gold labels."""
    if verdict is Verdict.OFF_QUESTION:
        if reason is not None or also_differs:
            raise ValueError("an off_question pair takes no reason and no also_differs")
        return
    if reason is None:
        raise ValueError(f"a {verdict.value} pair names a reason")
    check_also_differs(reason, also_differs)
    # A genuine conflict may have no standout reason; an apparent one is dissolved by a known
    # difference.
    if verdict is Verdict.APPARENT and reason is Reason.UNCLEAR:
        raise ValueError("an apparent pair names the reason the conflict dissolves")
