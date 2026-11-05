from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from claimgraph.config import DimensionType, QuestionConfig, load_config, resolve_question
from claimgraph.models import DEFAULT_EXCLUDED_DESIGNS, Design, Reason

QUESTIONS = Path(__file__).parents[1] / "questions"


def dimension(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": "synthetic_dimension",
        "definition": "A synthetic dimension for tests.",
        "type": "text",
        "refines": "population",
    }
    return fields | overrides


def config(**overrides: Any) -> QuestionConfig:
    fields: dict[str, Any] = {
        "id": "synthetic-question",
        "title": "Synthetic question for tests?",
        "query": "synthetic[tiab]",
        "end_date": date(2026, 10, 31),
    }
    return QuestionConfig.model_validate(fields | overrides)


def test_vitamin_d_config_loads() -> None:
    cfg = load_config(QUESTIONS / "vitamin-d-depression.toml")

    assert cfg.id == "vitamin-d-depression"
    assert cfg.start_date is None
    assert cfg.end_date == date(2026, 10, 31)
    assert cfg.exclude_designs == DEFAULT_EXCLUDED_DESIGNS
    [baseline] = cfg.dimensions
    assert baseline.name == "baseline_vitamin_d_status"
    assert baseline.type is DimensionType.OPTIONS
    assert baseline.refines is Reason.POPULATION
    assert baseline.options == [
        "below_20_ng_ml",
        "at_or_above_20_ng_ml",
        "mixed",
        "not_reported",
    ]


def test_creatine_config_loads() -> None:
    cfg = load_config(QUESTIONS / "creatine-cognition.toml")

    assert cfg.id == "creatine-cognition"
    assert cfg.dimensions == []


def test_defaults() -> None:
    cfg = config()

    assert cfg.filters == []
    assert cfg.start_date is None
    assert cfg.exclude_designs == DEFAULT_EXCLUDED_DESIGNS
    assert cfg.dimensions == []


def test_exclude_designs_can_be_set_per_question() -> None:
    cfg = config(exclude_designs=["animal"])

    assert cfg.exclude_designs == {Design.ANIMAL}


def test_load_rejects_id_that_differs_from_file_name(tmp_path: Path) -> None:
    path = tmp_path / "other-name.toml"
    path.write_text('id = "synthetic-question"\ntitle = "t"\nquery = "q"\nend_date = 2026-10-31\n')

    with pytest.raises(ValueError, match="other-name"):
        load_config(path)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"id": "Synthetic Question"}, id="id not kebab-case"),
        pytest.param({"query": "  "}, id="blank query"),
        pytest.param({"filters": [""]}, id="blank filter"),
        pytest.param({"start_date": date(2027, 1, 1)}, id="start after end"),
        pytest.param({"exclude_designs": ["anecdote"]}, id="unknown design"),
        pytest.param({"color": "teal"}, id="unknown key"),
    ],
)
def test_rejects_bad_question_fields(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        config(**overrides)


def test_accepts_five_dimensions() -> None:
    dims = [dimension(name=f"synthetic_{i}") for i in range(5)]

    assert len(config(dimensions=dims).dimensions) == 5


def test_options_dimension() -> None:
    options = ["low", "high", "not_reported"]
    cfg = config(dimensions=[dimension(type="options", options=options)])

    assert cfg.dimensions[0].options == options


def test_number_dimension() -> None:
    cfg = config(dimensions=[dimension(type="number", unit="ng/mL")])

    assert cfg.dimensions[0].unit == "ng/mL"


@pytest.mark.parametrize(
    "dims",
    [
        pytest.param([dimension(name=f"synthetic_{i}") for i in range(6)], id="more than five"),
        pytest.param([dimension(), dimension()], id="duplicate names"),
        pytest.param([dimension(name="quote")], id="clashes with a finding field"),
        pytest.param([dimension(name="Baseline D")], id="not snake_case"),
        pytest.param([dimension(definition="")], id="blank definition"),
        pytest.param([dimension(refines="unclear")], id="refines unclear"),
        pytest.param([dimension(refines="dose")], id="refines a new reason"),
        pytest.param([dimension(type="colour")], id="unknown type"),
        pytest.param([dimension(type="options")], id="options type without options"),
        pytest.param([dimension(type="options", options=[])], id="empty options"),
        pytest.param(
            [dimension(type="options", options=["a", "a", "not_reported"])],
            id="duplicate options",
        ),
        pytest.param(
            [dimension(type="options", options=["low", "high"])],
            id="options without not_reported",
        ),
        pytest.param([dimension(options=["a", "b"])], id="options on a text dimension"),
        pytest.param([dimension(type="number")], id="number without unit"),
        pytest.param([dimension(unit="ng/mL")], id="unit on a text dimension"),
    ],
)
def test_rejects_bad_dimensions(dims: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        config(dimensions=dims)


@pytest.mark.parametrize(
    ("question", "path"),
    [
        ("vitamin-d-depression", Path("questions/vitamin-d-depression.toml")),
        ("elsewhere/synthetic.toml", Path("elsewhere/synthetic.toml")),
    ],
)
def test_question_is_an_id_or_a_config_path(question: str, path: Path) -> None:
    assert resolve_question(question) == path
