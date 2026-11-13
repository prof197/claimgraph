import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from claimgraph.config import OUTSIDE_DOMAIN, QuestionConfig, load_config
from claimgraph.design_tree import QUESTIONS, full_answers
from claimgraph.dimensions import build_extraction_model, build_finding_model
from claimgraph.models import Design

ROOT = Path(__file__).parents[1]
SNAPSHOTS = Path(__file__).parent / "snapshots"


def config(*dimensions: dict[str, Any]) -> QuestionConfig:
    return QuestionConfig.model_validate(
        {
            "id": "synthetic-question",
            "title": "Synthetic question for tests?",
            "exposure_terms": ["synthetic"],
            "outcome_terms": ["placebo*"],
            "outcome_domain": ["synthetic symptom score"],
            "end_date": date(2026, 10, 31),
            "dimensions": list(dimensions),
        }
    )


def dimension(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": "synthetic_dimension",
        "definition": "A synthetic dimension for tests.",
        "type": "text",
        "refines": "population",
    }
    return fields | overrides


def extracted_finding(**dimension_values: Any) -> dict[str, Any]:
    return {
        "claim": "Synthetic claim.",
        "quote": "Synthetic quote.",
        "outcome_reported": "synthetic score",
        "exposure_side": "more",
        "outcome_direction": "no_change",
        "comparison": "between_groups_or_across_participants",
        "is_primary": True,
        "is_subgroup": False,
        "subgroup": None,
        "population": "synthetic adults",
        "intervention": "synthetic vitamin D3",
        "comparator": "placebo",
        "domain_outcome": "synthetic symptom score",
        **dimension_values,
    }


def test_extraction_schema_matches_snapshot() -> None:
    cfg = load_config(ROOT / "questions" / "vitamin-d-depression.toml")
    schema = json.dumps(build_extraction_model(cfg).model_json_schema(), indent=2) + "\n"
    snapshot = SNAPSHOTS / "vitamin-d-depression.extraction-schema.json"

    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(schema)
    assert schema == snapshot.read_text()


def test_without_dimensions_only_fixed_fields_and_the_domain() -> None:
    model = build_finding_model(config())

    assert list(model.model_fields) == [
        "claim",
        "quote",
        "outcome_reported",
        "exposure_side",
        "outcome_direction",
        "comparison",
        "is_primary",
        "is_subgroup",
        "subgroup",
        "population",
        "intervention",
        "comparator",
        "domain_outcome",
    ]


def test_the_domain_field_offers_the_domain_and_an_explicit_way_out() -> None:
    cfg = config()
    model = build_finding_model(cfg)
    field = model.model_json_schema()["properties"]["domain_outcome"]

    assert field["enum"] == [*cfg.outcome_domain, OUTSIDE_DOMAIN]
    with pytest.raises(ValidationError):
        model.model_validate(extracted_finding(domain_outcome="synthetic anxiety score"))
    assert model.model_validate(extracted_finding(domain_outcome=OUTSIDE_DOMAIN))


def test_a_domain_entry_cannot_be_the_way_out() -> None:
    with pytest.raises(ValidationError, match="outside the domain"):
        QuestionConfig.model_validate(
            config().model_dump() | {"outcome_domain": ["synthetic score", "Outside the domain"]}
        )


def test_dimension_fields_follow_fixed_fields() -> None:
    model = build_finding_model(config(dimension(name="first"), dimension(name="second")))

    assert list(model.model_fields)[-3:] == ["domain_outcome", "first", "second"]


def test_dimension_definition_becomes_its_description() -> None:
    model = build_finding_model(config(dimension(definition="Synthetic definition.")))

    assert model.model_fields["synthetic_dimension"].description == "Synthetic definition."


def test_number_dimension_description_names_its_unit() -> None:
    model = build_finding_model(config(dimension(type="number", unit="ng/mL")))

    description = model.model_fields["synthetic_dimension"].description
    assert description == "A synthetic dimension for tests. Unit: ng/mL."


def test_dimension_must_be_answered() -> None:
    model = build_finding_model(config(dimension()))

    with pytest.raises(ValidationError):
        model.model_validate(extracted_finding())


@pytest.mark.parametrize(
    ("dim", "value"),
    [
        (dimension(), "synthetic text"),
        (dimension(), None),
        (dimension(type="options", options=["low", "high", "not_reported"]), "high"),
        (dimension(type="options", options=["low", "high", "not_reported"]), "not_reported"),
        (dimension(type="number", unit="ng/mL"), 18.5),
        (dimension(type="number", unit="ng/mL"), None),
    ],
)
def test_dimension_accepts_valid_values(dim: dict[str, Any], value: object) -> None:
    model = build_finding_model(config(dim))

    parsed = model.model_validate(extracted_finding(synthetic_dimension=value))
    assert parsed.model_dump()["synthetic_dimension"] == value


@pytest.mark.parametrize(
    ("dim", "value"),
    [
        (dimension(type="options", options=["low", "high", "not_reported"]), "medium"),
        (dimension(type="options", options=["low", "high", "not_reported"]), None),
        (dimension(type="number", unit="ng/mL"), "about twenty"),
    ],
)
def test_dimension_rejects_invalid_values(dim: dict[str, Any], value: object) -> None:
    model = build_finding_model(config(dim))

    with pytest.raises(ValidationError):
        model.model_validate(extracted_finding(synthetic_dimension=value))


def design_answers(design: Design = Design.RCT) -> dict[str, str]:
    """Every question answered, as the model answers them."""
    return full_answers(design)


def extraction(**overrides: Any) -> dict[str, Any]:
    return {
        "design_answers": design_answers(),
        "sample_size": 120,
        "review_summary": None,
        "findings": [],
        "other_outcomes": [],
    } | overrides


def test_extraction_validates_a_full_response() -> None:
    model = build_extraction_model(config(dimension()))

    parsed = model.model_validate(
        extraction(findings=[extracted_finding(synthetic_dimension="synthetic text")])
    )
    assert len(parsed.model_dump()["findings"]) == 1


def test_extraction_rejects_unknown_fields() -> None:
    model = build_extraction_model(config())

    with pytest.raises(ValidationError):
        model.model_validate(extraction(direction="benefit"))
    # The design is settled from the answers and the publication types, not named by the model.
    with pytest.raises(ValidationError):
        model.model_validate(extraction(design="rct"))


def test_every_design_question_needs_one_of_its_answers() -> None:
    model = build_extraction_model(config())
    answers = design_answers()

    assert model.model_validate(extraction(design_answers=answers))
    for missing in ("subjects", "pools", "measured_before"):
        with pytest.raises(ValidationError):
            model.model_validate(
                extraction(design_answers={k: v for k, v in answers.items() if k != missing})
            )
    for value in (None, "not_reached", "maybe"):
        with pytest.raises(ValidationError):
            model.model_validate(extraction(design_answers=answers | {"pools": value}))


def test_the_design_answers_offer_each_question_options() -> None:
    schema = build_extraction_model(config()).model_json_schema()
    answers = schema["$defs"]["DesignAnswers"]

    assert answers["required"] == [q.key for q in QUESTIONS]
    for question in QUESTIONS:
        field = answers["properties"][question.key]
        assert field["enum"] == [o.value for o in question.options]
        assert field["description"] == question.text


def test_other_outcomes_are_a_required_list() -> None:
    model = build_extraction_model(config())
    fields = extraction()
    del fields["other_outcomes"]

    with pytest.raises(ValidationError):
        model.model_validate(fields)
    parsed = model.model_validate(extraction(other_outcomes=["synthetic fatigue score"]))
    assert parsed.model_dump()["other_outcomes"] == ["synthetic fatigue score"]
