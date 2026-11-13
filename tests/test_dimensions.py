import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from claimgraph.config import QuestionConfig, load_config
from claimgraph.design_tree import QUESTIONS, answers_for
from claimgraph.dimensions import NOT_REACHED, build_extraction_model, build_finding_model
from claimgraph.models import Design, FindingFields

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
        "effect": "no_change",
        "is_primary": True,
        "is_subgroup": False,
        "subgroup": None,
        "population": "synthetic adults",
        "intervention": "synthetic vitamin D3",
        "comparator": "placebo",
        **dimension_values,
    }


def test_extraction_schema_matches_snapshot() -> None:
    cfg = load_config(ROOT / "questions" / "vitamin-d-depression.toml")
    schema = json.dumps(build_extraction_model(cfg).model_json_schema(), indent=2) + "\n"
    snapshot = SNAPSHOTS / "vitamin-d-depression.extraction-schema.json"

    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(schema)
    assert schema == snapshot.read_text()


def test_without_dimensions_only_fixed_fields() -> None:
    model = build_finding_model(config())

    assert model.model_fields.keys() == FindingFields.model_fields.keys()


def test_dimension_fields_follow_fixed_fields() -> None:
    model = build_finding_model(config(dimension(name="first"), dimension(name="second")))

    assert list(model.model_fields)[-2:] == ["first", "second"]


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
    """Every question answered: the path to the design, and not_reached for the rest."""
    return {q.key: NOT_REACHED for q in QUESTIONS} | answers_for(design)


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


def test_every_design_question_needs_an_answer_or_not_reached() -> None:
    model = build_extraction_model(config())
    answers = design_answers()

    assert model.model_validate(extraction(design_answers=answers))
    for missing in ("subjects", "measured_before"):
        with pytest.raises(ValidationError):
            model.model_validate(
                extraction(design_answers={k: v for k, v in answers.items() if k != missing})
            )
    with pytest.raises(ValidationError):
        model.model_validate(extraction(design_answers=answers | {"measured_before": None}))
    with pytest.raises(ValidationError):
        model.model_validate(extraction(design_answers=answers | {"pools": "maybe"}))


def test_the_design_answers_offer_each_question_options() -> None:
    schema = build_extraction_model(config()).model_json_schema()
    answers = schema["$defs"]["DesignAnswers"]

    assert answers["required"] == [q.key for q in QUESTIONS]
    for question in QUESTIONS:
        field = answers["properties"][question.key]
        assert field["enum"] == [o.value for o in question.options] + [NOT_REACHED]
        assert field["description"] == question.text


def test_other_outcomes_are_a_required_list() -> None:
    model = build_extraction_model(config())
    fields = extraction()
    del fields["other_outcomes"]

    with pytest.raises(ValidationError):
        model.model_validate(fields)
    parsed = model.model_validate(extraction(other_outcomes=["synthetic fatigue score"]))
    assert parsed.model_dump()["other_outcomes"] == ["synthetic fatigue score"]
