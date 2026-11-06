"""Gold label files: abstracts, outcome pairs and contradiction pairs."""

import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_retrieve_parse import article

from claimgraph.config import QuestionConfig, load_config
from claimgraph.gold import GoldAbstract, Licence, Stratum
from claimgraph.labels import (
    AbstractLabel,
    check_abstract_label,
    check_labels,
    label_template,
    load_abstract_label,
    load_contradiction_pairs,
    load_outcome_pairs,
)
from claimgraph.models import Design, Effect, Reason, Verdict
from claimgraph.retrieve import Abstract, parse_record

ROOT = Path(__file__).parents[1]
EXAMPLES = Path(__file__).parent / "fixtures" / "labels"
VITAMIN_D = load_config(ROOT / "questions" / "vitamin-d-depression.toml")

SYNTHETIC_ABSTRACT = (
    "<AbstractText Label='RESULTS'>Synthetic depression scores fell more with synthetic "
    "vitamin D than with placebo (p = 0.01). Synthetic remission rates did not differ between "
    "groups.</AbstractText>"
)


def synthetic_abstract(pmid: str = "999000001", abstract: str = SYNTHETIC_ABSTRACT) -> Abstract:
    record = parse_record(article(pmid=pmid, abstract=abstract))
    assert isinstance(record, Abstract)
    return record


def label(**overrides: Any) -> AbstractLabel:
    fields: dict[str, Any] = {
        "pmid": "999000001",
        "design": "rct",
        "sample_size": 120,
        "finding": [
            {
                "quote": "Synthetic remission rates did not differ between groups.",
                "outcome_reported": "synthetic remission",
                "effect": "no_change",
                "is_primary": False,
                "dimensions": {"baseline_vitamin_d_status": "not_reported"},
            }
        ],
    }
    return AbstractLabel.model_validate(fields | overrides)


def config_with(*dimensions: dict[str, Any]) -> QuestionConfig:
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


# Abstract labels


def test_the_example_label_validates_against_its_abstract() -> None:
    example = load_abstract_label(EXAMPLES / "999000001.toml")

    assert example.design is Design.RCT
    assert example.participants == 120
    assert [f.effect for f in example.findings] == [Effect.DECREASED, Effect.NO_CHANGE]
    assert check_abstract_label(example, synthetic_abstract(), VITAMIN_D) == []


def test_not_reported_sample_size_reads_as_none() -> None:
    assert label(sample_size="not_reported").participants is None


def test_an_off_topic_abstract_has_no_findings() -> None:
    off_topic = label(finding=[], note="Reports nothing in the outcome domain.")

    assert off_topic.findings == []
    assert check_abstract_label(off_topic, synthetic_abstract(), VITAMIN_D) == []


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"design": "narrative_review"}, id="unknown design"),
        pytest.param({"design": ""}, id="unfilled design"),
        pytest.param({"sample_size": 0}, id="zero sample size"),
        pytest.param({"sample_size": ""}, id="unfilled sample size"),
        pytest.param({"sample_size": "unknown"}, id="other sample size text"),
        pytest.param({"color": "teal"}, id="unknown key"),
        pytest.param({"design": "meta_analysis"}, id="review with findings"),
        pytest.param({"review_summary": "Synthetic summary."}, id="summary on a trial"),
    ],
)
def test_rejects_bad_abstract_labels(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        label(**overrides)


@pytest.mark.parametrize(
    "finding",
    [
        pytest.param({"quote": ""}, id="unfilled quote"),
        pytest.param({"effect": "improved"}, id="unknown effect"),
        pytest.param({"effect": ""}, id="unfilled effect"),
        pytest.param({"outcome_reported": " "}, id="blank outcome"),
        pytest.param({"is_primary": "yes"}, id="primary not a boolean"),
    ],
)
def test_rejects_bad_findings(finding: dict[str, Any]) -> None:
    base = label().model_dump()["findings"][0]

    with pytest.raises(ValidationError):
        label(finding=[base | finding])


def test_a_review_carries_a_summary_and_no_findings() -> None:
    review = label(design="systematic_review", finding=[], review_summary="Synthetic summary.")

    assert review.review_summary == "Synthetic summary."


def test_quote_must_appear_in_the_abstract() -> None:
    finding = label().model_dump()["findings"][0] | {"quote": "A sentence the abstract lacks."}

    problems = check_abstract_label(label(finding=[finding]), synthetic_abstract(), VITAMIN_D)

    assert problems == ["finding 1: quote not found in the abstract"]


def test_quote_matching_ignores_whitespace_and_quote_marks() -> None:
    abstract = synthetic_abstract(
        abstract="<AbstractText>Participants rated mood as “better”  overall.</AbstractText>"
    )
    finding = label().model_dump()["findings"][0] | {
        "quote": 'Participants rated mood as "better"\noverall.'
    }

    assert check_abstract_label(label(finding=[finding]), abstract, VITAMIN_D) == []


def test_label_for_another_record_is_a_problem() -> None:
    problems = check_abstract_label(label(), synthetic_abstract(pmid="999000002"), VITAMIN_D)

    assert "label is for PMID 999000001, not 999000002" in problems


@pytest.mark.parametrize(
    ("dimensions", "problem"),
    [
        pytest.param({}, "finding 1: missing dimension baseline_vitamin_d_status", id="missing"),
        pytest.param(
            {"baseline_vitamin_d_status": "not_reported", "synthetic": "x"},
            "finding 1: unknown dimension synthetic",
            id="unknown",
        ),
        pytest.param(
            {"baseline_vitamin_d_status": "low"},
            "finding 1: baseline_vitamin_d_status must be one of below_20_ng_ml, "
            "at_or_above_20_ng_ml, mixed, not_reported",
            id="not an option",
        ),
    ],
)
def test_dimension_values_follow_the_config(dimensions: dict[str, Any], problem: str) -> None:
    finding = label().model_dump()["findings"][0] | {"dimensions": dimensions}

    problems = check_abstract_label(label(finding=[finding]), synthetic_abstract(), VITAMIN_D)

    assert problems == [problem]


@pytest.mark.parametrize(
    ("value", "ok"),
    [(12.5, True), (20, True), ("not_reported", True), ("twelve", False), (True, False)],
)
def test_number_dimension_takes_a_number_or_not_reported(value: Any, ok: bool) -> None:
    config = config_with(
        {
            "name": "baseline_level",
            "definition": "A synthetic level.",
            "type": "number",
            "unit": "ng/mL",
            "refines": "population",
        }
    )
    finding = label().model_dump()["findings"][0] | {"dimensions": {"baseline_level": value}}

    problems = check_abstract_label(label(finding=[finding]), synthetic_abstract(), config)

    assert (problems == []) is ok


def test_text_dimension_takes_text_or_not_reported() -> None:
    config = config_with(
        {
            "name": "setting",
            "definition": "A synthetic setting.",
            "type": "text",
            "refines": "population",
        }
    )
    base = label().model_dump()["findings"][0]

    for value, ok in [("clinic", True), ("not_reported", True), ("", False), (3, False)]:
        finding = base | {"dimensions": {"setting": value}}
        problems = check_abstract_label(label(finding=[finding]), synthetic_abstract(), config)
        assert (problems == []) is ok, value


def test_a_repeated_finding_is_a_problem() -> None:
    finding = label().model_dump()["findings"][0]

    problems = check_abstract_label(
        label(finding=[finding, finding]), synthetic_abstract(), VITAMIN_D
    )

    assert problems == ["finding 2 repeats finding 1"]


# Outcome pairs


def test_the_example_outcome_pairs_load() -> None:
    pairs = load_outcome_pairs(EXAMPLES / "outcome_pairs.toml")

    assert [p.merge for p in pairs] == [True, False]


def test_an_outcome_pair_needs_two_strings(tmp_path: Path) -> None:
    path = tmp_path / "outcome_pairs.toml"
    path.write_text('[[pair]]\na = "synthetic score"\nb = "synthetic score"\nmerge = true\n')

    with pytest.raises(ValueError, match="different"):
        load_outcome_pairs(path)


def test_an_outcome_pair_is_labeled_once_in_either_order(tmp_path: Path) -> None:
    path = tmp_path / "outcome_pairs.toml"
    path.write_text(
        '[[pair]]\na = "synthetic a"\nb = "synthetic b"\nmerge = true\n'
        '[[pair]]\na = "synthetic b"\nb = "synthetic a"\nmerge = false\n'
    )

    with pytest.raises(ValueError, match="more than once"):
        load_outcome_pairs(path)


# Contradiction pairs


def test_the_example_contradiction_pairs_load() -> None:
    pairs = load_contradiction_pairs(EXAMPLES / "contradiction_pairs.toml")

    assert [p.verdict for p in pairs] == [Verdict.GENUINE, Verdict.APPARENT]
    assert pairs[0].also_differs == [Reason.INTERVENTION]
    assert pairs[0].pair_id == "synthetic depressive symptoms|999000001|999000002"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"study_a": "999000002", "study_b": "999000001"}, id="studies out of order"),
        pytest.param({"also_differs": ["population"]}, id="also_differs repeats the reason"),
        pytest.param({"also_differs": ["unclear"]}, id="unclear as a difference"),
        pytest.param({"verdict": "likely"}, id="unknown verdict"),
        pytest.param({"reason": "dose"}, id="unknown reason"),
    ],
)
def test_rejects_bad_contradiction_labels(tmp_path: Path, overrides: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "outcome": "synthetic depressive symptoms",
        "study_a": "999000001",
        "study_b": "999000002",
        "verdict": "genuine",
        "reason": "population",
    } | overrides
    lines = [f"{k} = {_toml(v)}" for k, v in fields.items()]
    path = tmp_path / "contradiction_pairs.toml"
    path.write_text("[[pair]]\n" + "\n".join(lines) + "\n")

    with pytest.raises(ValueError):
        load_contradiction_pairs(path)


def test_a_contradiction_pair_is_labeled_once(tmp_path: Path) -> None:
    pair = (
        '[[pair]]\noutcome = "synthetic"\nstudy_a = "999000001"\nstudy_b = "999000002"\n'
        'verdict = "genuine"\nreason = "population"\n'
    )
    path = tmp_path / "contradiction_pairs.toml"
    path.write_text(pair + pair)

    with pytest.raises(ValueError, match="more than once"):
        load_contradiction_pairs(path)


def _toml(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(f'"{v}"' for v in value) + "]"
    return f'"{value}"'


# Templates


def gold_entry(design: Stratum = Stratum.TRIAL, abstract: Abstract | None = None) -> GoldAbstract:
    return GoldAbstract(
        abstract=abstract or synthetic_abstract(),
        licence=Licence(pmcid="PMC999000001", code="CC BY", versions=[1]),
        design=design,
        slot=design,
        on_topic=True,
    )


def test_template_shows_the_abstract_and_fails_until_filled() -> None:
    text = label_template(gold_entry(), VITAMIN_D)
    data = tomllib.loads(text)

    assert "# RESULTS: Synthetic depression scores fell" in text
    assert data["pmid"] == "999000001"
    assert set(data["finding"][0]["dimensions"]) == {"baseline_vitamin_d_status"}
    with pytest.raises(ValidationError):
        AbstractLabel.model_validate(data)


def test_review_template_asks_for_a_summary_not_findings() -> None:
    data = tomllib.loads(label_template(gold_entry(Stratum.SYSTEMATIC_REVIEW), VITAMIN_D))

    assert "finding" not in data
    assert data["review_summary"] == ""


def test_template_wraps_long_abstract_lines_as_comments() -> None:
    words = " ".join(f"synthetic{n}" for n in range(60))
    entry = gold_entry(
        abstract=synthetic_abstract(abstract=f"<AbstractText>{words}.</AbstractText>")
    )

    text = label_template(entry, VITAMIN_D)

    assert all(len(line) <= 100 for line in text.splitlines() if line.startswith("#"))


# Checking a question's labels


def write_gold(out: Path, *entries: GoldAbstract) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "gold.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in entries))


def test_check_reports_labeled_missing_and_stray_files(tmp_path: Path) -> None:
    out = tmp_path / "vitamin-d-depression"
    write_gold(
        out,
        gold_entry(),
        gold_entry(abstract=synthetic_abstract(pmid="999000002")),
    )
    labels = out / "labels"
    labels.mkdir()
    (labels / "999000001.toml").write_text((EXAMPLES / "999000001.toml").read_text())
    stray = (EXAMPLES / "999000001.toml").read_text().replace("999000001", "999000009")
    (labels / "999000009.toml").write_text(stray)

    report = check_labels(out, VITAMIN_D)

    assert report.labeled == ["999000001"]
    assert report.missing == ["999000002"]
    assert report.problems == {"999000009.toml": ["PMID 999000009 is not in the gold sample"]}


def test_check_reports_an_unfilled_template(tmp_path: Path) -> None:
    out = tmp_path / "vitamin-d-depression"
    write_gold(out, gold_entry())
    (out / "labels").mkdir()
    (out / "labels" / "999000001.toml").write_text(label_template(gold_entry(), VITAMIN_D))

    report = check_labels(out, VITAMIN_D)

    assert report.labeled == []
    assert "999000001.toml" in report.problems


def test_committed_labels_are_valid() -> None:
    for out in sorted((ROOT / "evals" / "gold").glob("*/")):
        if not (out / "labels").exists():
            continue
        report = check_labels(out, load_config(ROOT / "questions" / f"{out.name}.toml"))
        assert report.problems == {}, out.name


def test_check_requires_the_file_name_to_match_the_pmid(tmp_path: Path) -> None:
    out = tmp_path / "vitamin-d-depression"
    write_gold(out, gold_entry())
    (out / "labels").mkdir()
    (out / "labels" / "label.toml").write_text((EXAMPLES / "999000001.toml").read_text())

    report = check_labels(out, VITAMIN_D)

    assert report.problems == {"label.toml": ["file name must be 999000001.toml"]}


# Command line


def test_template_then_check_from_the_command_line(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from claimgraph import cli

    out = tmp_path / "vitamin-d-depression"
    write_gold(out, gold_entry(), gold_entry(abstract=synthetic_abstract(pmid="999000002")))
    (out / "labels").mkdir()
    (out / "labels" / "999000001.toml").write_text((EXAMPLES / "999000001.toml").read_text())
    config = str(ROOT / "questions" / "vitamin-d-depression.toml")
    runner = CliRunner()

    written = runner.invoke(cli.app, ["gold", "template", config, "--gold-dir", str(tmp_path)])
    checked = runner.invoke(cli.app, ["gold", "check", config, "--gold-dir", str(tmp_path)])

    assert written.exit_code == 0, written.output
    assert "wrote 1 templates" in written.output
    assert (out / "labels" / "999000001.toml").read_text() == (
        EXAMPLES / "999000001.toml"
    ).read_text()
    assert checked.exit_code == 1
    assert "1 labeled, 1 with problems, 0 not started, of 2" in checked.output
