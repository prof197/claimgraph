"""Model round-trip and validation tests. All records here are synthetic."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from claimgraph.models import (
    DEFAULT_EXCLUDED_DESIGNS,
    DESIGN_WEIGHTS,
    REVIEW_DESIGNS,
    Arm,
    CandidatePair,
    Classification,
    Design,
    Effect,
    Finding,
    NormalizedOutcome,
    PairKind,
    Reason,
    Study,
    Verdict,
)


def study(**overrides: Any) -> Study:
    fields: dict[str, Any] = {
        "pmid": "999000001",
        "title": "Synthetic trial of vitamin D for test fixtures",
        "first_author": "Synthetic",
        "journal": "Journal of Test Fixtures",
        "trial_ids": ["CLINICALTRIALS.GOV:NCT99000001"],
        "year": 2020,
        "language": "eng",
        "design": Design.RCT,
        "sample_size": 120,
    }
    return Study.model_validate(fields | overrides)


def finding(**overrides: Any) -> Finding:
    fields: dict[str, Any] = {
        "finding_id": "999000001-1",
        "claim": "Synthetic claim: scores fell more with vitamin D than placebo.",
        "quote": "Synthetic quote: scores fell more with vitamin D than with placebo.",
        "outcome_reported": "synthetic depression score",
        "effect": Effect.DECREASED,
        "is_primary": True,
        "population": "synthetic adults",
        "intervention": "synthetic vitamin D3, 2,000 IU daily",
        "comparator": "placebo",
        "dimensions": {"baseline_vitamin_d_status": "not_reported"},
    }
    return Finding.model_validate(fields | overrides)


def outcome(**overrides: Any) -> NormalizedOutcome:
    fields: dict[str, Any] = {
        "name": "Depressive symptoms",
        "members": ["synthetic depression score", "synthetic mood score"],
        "higher_is_better": False,
        "reviewed": True,
    }
    return NormalizedOutcome.model_validate(fields | overrides)


def pair(**overrides: Any) -> CandidatePair:
    fields: dict[str, Any] = {
        "outcome": "Depressive symptoms",
        "study_a": "999000001",
        "study_b": "999000002",
        "kind": PairKind.EFFECT_VS_NONE,
    }
    return CandidatePair.model_validate(fields | overrides)


def classification(**overrides: Any) -> Classification:
    fields: dict[str, Any] = {
        "pair_id": pair().pair_id,
        "verdict": Verdict.GENUINE,
        "reason": Reason.POPULATION,
        "also_differs": [Reason.INTERVENTION],
        "detail": "synthetic: depressed adults vs a general older population",
        "rationale": "Synthetic rationale: the populations differ in baseline depression.",
        "arm": Arm.SINGLE_SMALL,
    }
    return Classification.model_validate(fields | overrides)


@pytest.mark.parametrize("record", [study(), finding(), outcome(), pair(), classification()])
def test_json_round_trip(record: BaseModel) -> None:
    assert type(record).model_validate_json(record.model_dump_json()) == record


def test_study_label_is_first_author_and_year() -> None:
    assert study().label == "Synthetic 2020"


def test_study_label_falls_back_to_pmid_without_an_author() -> None:
    assert study(first_author=None).label == "PMID 999000001"


@pytest.mark.parametrize("pmid", ["", "abc", "0123", "12 34"])
def test_study_rejects_malformed_pmid(pmid: str) -> None:
    with pytest.raises(ValidationError):
        study(pmid=pmid)


@pytest.mark.parametrize(
    "trial_id",
    ["NCT99000001", "clinicaltrials.gov:NCT99000001", "ISRCTN:isrctn99000001", "ISRCTN:", " :X"],
)
def test_study_rejects_ids_not_in_registry_accession_form(trial_id: str) -> None:
    with pytest.raises(ValidationError):
        study(trial_ids=[trial_id])


@pytest.mark.parametrize(
    "trial_id", ["CLINICALTRIALS.GOV:NCT99000001", "IRCT:IRCT99000001N1", "UMIN CTR:UMIN99000001"]
)
def test_study_accepts_registry_accession_ids(trial_id: str) -> None:
    assert study(trial_ids=[trial_id]).trial_ids == [trial_id]


@pytest.mark.parametrize("sample_size", [0, -5])
def test_study_rejects_non_positive_sample_size(sample_size: int) -> None:
    with pytest.raises(ValidationError):
        study(sample_size=sample_size)


def test_study_allows_unknown_sample_size() -> None:
    assert study(sample_size=None).sample_size is None


def test_finding_pmid_comes_from_finding_id() -> None:
    assert finding(finding_id="999000001-3").pmid == "999000001"


@pytest.mark.parametrize("finding_id", ["999000001", "999000001-0", "abc-1", "999000001-1-2"])
def test_finding_rejects_malformed_id(finding_id: str) -> None:
    with pytest.raises(ValidationError):
        finding(finding_id=finding_id)


@pytest.mark.parametrize("field", ["claim", "quote", "outcome_reported"])
@pytest.mark.parametrize("value", ["", "   "])
def test_finding_rejects_blank_text(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        finding(**{field: value})


def test_finding_keeps_quote_whitespace_unchanged() -> None:
    quote = "  Synthetic quote with surrounding space.  "
    assert finding(quote=quote).quote == quote


def test_finding_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        finding(direction="benefit")


def test_outcome_rejects_duplicate_members() -> None:
    with pytest.raises(ValidationError):
        outcome(members=["synthetic score", "synthetic score"])


def test_outcome_rejects_empty_members() -> None:
    with pytest.raises(ValidationError):
        outcome(members=[])


def test_pair_rejects_a_study_paired_with_itself() -> None:
    with pytest.raises(ValidationError):
        pair(study_a="999000001", study_b="999000001")


def test_pair_rejects_non_canonical_order() -> None:
    with pytest.raises(ValidationError):
        pair(study_a="999000002", study_b="999000001")


def test_pair_id_joins_outcome_and_study_keys() -> None:
    assert pair().pair_id == "Depressive symptoms|999000001|999000002"


def test_classification_rejects_primary_reason_in_also_differs() -> None:
    with pytest.raises(ValidationError):
        classification(reason=Reason.POPULATION, also_differs=[Reason.POPULATION])


def test_classification_rejects_repeated_also_differs() -> None:
    with pytest.raises(ValidationError):
        classification(also_differs=[Reason.INTERVENTION, Reason.INTERVENTION])


def test_classification_rejects_unclear_as_an_extra_difference() -> None:
    with pytest.raises(ValidationError):
        classification(also_differs=[Reason.UNCLEAR])


def test_arm_values_are_the_cli_depth_names() -> None:
    assert [arm.value for arm in Arm] == [
        "single-small",
        "single-mid",
        "self-consistency",
        "self-moa",
        "council",
        "council-ranked",
    ]


def test_design_weights_match_the_published_table() -> None:
    assert DESIGN_WEIGHTS == {
        Design.RCT: 3,
        Design.NON_RANDOMIZED_TRIAL: 2,
        Design.COHORT: 2,
        Design.CASE_CONTROL: 1,
        Design.CROSS_SECTIONAL: 1,
        Design.CASE_SERIES: 0.5,
    }


def test_reviews_and_default_exclusions() -> None:
    assert {Design.SYSTEMATIC_REVIEW, Design.META_ANALYSIS} == REVIEW_DESIGNS
    assert {Design.CASE_REPORT, Design.ANIMAL, Design.IN_VITRO} == DEFAULT_EXCLUDED_DESIGNS


def test_every_design_has_exactly_one_role() -> None:
    weighted = set(DESIGN_WEIGHTS)
    roles = [weighted, REVIEW_DESIGNS, DEFAULT_EXCLUDED_DESIGNS, {Design.OTHER}]
    for design in Design:
        assert sum(design in role for role in roles) == 1, design
