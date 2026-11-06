import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from claimgraph.config import QuestionConfig, load_config
from claimgraph.dimensions import build_extraction_model, build_finding_model
from claimgraph.models import FindingFields

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


def test_extraction_validates_a_full_response() -> None:
    model = build_extraction_model(config(dimension()))

    parsed = model.model_validate(
        {
            "design": "rct",
            "sample_size": 120,
            "review_summary": None,
            "findings": [extracted_finding(synthetic_dimension="synthetic text")],
        }
    )
    assert len(parsed.model_dump()["findings"]) == 1


def test_extraction_rejects_unknown_fields() -> None:
    model = build_extraction_model(config())

    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "design": "rct",
                "sample_size": 120,
                "review_summary": None,
                "findings": [],
                "direction": "benefit",
            }
        )
