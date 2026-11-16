import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from export_fixtures import FIXTURES, SYMPTOMS, minimal, synthetic
from pydantic import ValidationError

from claimgraph import export
from claimgraph.export import Export

ROOT = Path(__file__).parents[1]
FIXTURE_DIR = ROOT / "site" / "src" / "fixtures"
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"


def test_the_committed_schema_is_generated_from_the_models() -> None:
    path = ROOT / export.SCHEMA_FILE
    if UPDATE:
        path.write_text(export.schema_text())

    assert path.read_text() == export.schema_text()


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_the_committed_fixture_exports_match_their_builders(name: str) -> None:
    path = FIXTURE_DIR / f"{name}.json"
    text = FIXTURES[name]().model_dump_json(indent=2) + "\n"
    if UPDATE:
        path.write_text(text)

    assert path.read_text() == text


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_fixture_export_round_trips(name: str) -> None:
    text = (FIXTURE_DIR / f"{name}.json").read_text()

    assert Export.model_validate_json(text).model_dump_json(indent=2) + "\n" == text


@pytest.mark.parametrize("path", sorted(FIXTURE_DIR.glob("*.json")), ids=lambda p: p.name)
def test_fixture_exports_name_only_synthetic_records(path: Path) -> None:
    pmids = re.findall(r'"(?:pmid|key|study|study_a|study_b)": "(\d+)"', path.read_text())

    assert pmids
    assert all(p.startswith("999") for p in pmids)


def test_the_synthetic_fixture_covers_the_page_s_edge_cases() -> None:
    fixture = synthetic()
    outcomes = {o.name: o for o in fixture.outcomes}
    edges = [e for e in fixture.graph.edges if e.kind == "contradiction"]
    genuine = {e.source.rsplit("-", 1)[0] for e in edges} | {
        e.target.rsplit("-", 1)[0] for e in edges
    }

    assert any(o.summary.thin for o in fixture.outcomes)
    assert any(
        o.apparent and not any(e.pair_id.startswith(o.name) for e in edges)
        for o in fixture.outcomes
    )
    assert any(not d.evaluated for d in fixture.question.dimensions)
    assert fixture.reviews
    assert {e.reason for e in edges} == set(export.Reason)
    assert outcomes[SYMPTOMS].summary.views[-1].changes_leading
    assert genuine


def test_the_minimal_fixture_has_no_pairs_quotes_or_reviews_to_show() -> None:
    fixture = minimal()

    assert fixture.method.depth is None
    assert not fixture.reviews and not fixture.question.dimensions
    assert any(n.kind == "finding" and n.quote is None for n in fixture.graph.nodes)


def _dump() -> dict[str, Any]:
    return json.loads(synthetic().model_dump_json())


def _outcome(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(o for o in data["outcomes"] if o["name"] == name)


def test_a_thin_outcome_shows_counts_only() -> None:
    data = _dump()
    thin = next(o for o in data["outcomes"] if o["summary"]["thin"])
    thin["summary"]["views"] = _outcome(data, SYMPTOMS)["summary"]["views"]

    with pytest.raises(ValidationError, match="counts only"):
        Export.model_validate(data)


def test_an_outcome_under_five_studies_must_be_thin() -> None:
    data = _dump()
    summary = _outcome(data, SYMPTOMS)["summary"]
    summary |= {"studies": 4, "counts": {"benefit": 2, "none": 1, "harm": 1}}
    summary["designs"] = [{"design": "rct", "studies": 4}]

    with pytest.raises(ValidationError, match="thin exactly when"):
        Export.model_validate(data)


def test_shares_must_sum_to_one() -> None:
    data = _dump()
    _outcome(data, SYMPTOMS)["summary"]["views"][0]["shares"]["harm"] = 0.5

    with pytest.raises(ValidationError, match="sum to 1"):
        Export.model_validate(data)


def test_a_finding_must_name_a_known_study() -> None:
    data = _dump()
    finding = next(n for n in data["graph"]["nodes"] if n["kind"] == "finding")
    finding["study"] = "999999999"

    with pytest.raises(ValidationError, match="unknown outcome or study"):
        Export.model_validate(data)


def test_a_contradiction_joins_headline_findings() -> None:
    data = _dump()
    other = next(n for n in data["graph"]["nodes"] if n["kind"] == "finding" and not n["headline"])
    edge = next(e for e in data["graph"]["edges"] if e["kind"] == "contradiction")
    edge["target"] = other["id"]

    with pytest.raises(ValidationError, match="headline findings"):
        Export.model_validate(data)


def test_a_study_has_one_headline_finding_per_outcome() -> None:
    data = _dump()
    for node in data["graph"]["nodes"]:
        if node["kind"] == "finding":
            node["headline"] = True

    with pytest.raises(ValidationError, match="one headline finding"):
        Export.model_validate(data)


def test_every_outcome_has_its_hub() -> None:
    data = _dump()
    data["graph"]["nodes"] = [n for n in data["graph"]["nodes"] if n["id"] != "outcome:" + SYMPTOMS]

    with pytest.raises(ValidationError, match="exactly one hub"):
        Export.model_validate(data)
