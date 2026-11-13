"""The local labeling page's server side: sentences, guide excerpts, saving and requests."""

import json
from http import HTTPStatus
from pathlib import Path

import pytest
from test_labels import EXAMPLES, VITAMIN_D, gold_entry, synthetic_abstract, write_gold

from claimgraph.labels import LabelDraft, label_template, normalize_quote, read_gold, render_label
from claimgraph.review import (
    Mark,
    ReviewSession,
    Status,
    field_rules,
    handle,
    split_sentences,
    write_page,
)

ROOT = Path(__file__).parents[1]
GOLD = ROOT / "evals" / "gold"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "Scores fell (p = 0.01). Remission did not differ.",
            ["Scores fell (p = 0.01).", "Remission did not differ."],
            id="two sentences",
        ),
        pytest.param(
            "Mean 25(OH)D was 20.5 ng/mL vs. 31.2 ng/mL. Scores fell.",
            ["Mean 25(OH)D was 20.5 ng/mL vs. 31.2 ng/mL.", "Scores fell."],
            id="decimals and vs.",
        ),
        pytest.param(
            "Synthetic work by Smith J. Doe et al. Found nothing. Trials, e.g. Synthetic A, agree.",
            [
                "Synthetic work by Smith J. Doe et al. Found nothing.",
                "Trials, e.g. Synthetic A, agree.",
            ],
            id="initials and abbreviations",
        ),
        pytest.param(
            "Synthetic supplementation.We conducted a trial. U.S.Army data.",
            ["Synthetic supplementation.", "We conducted a trial.", "U.S.Army data."],
            id="missing space after a full stop",
        ),
        pytest.param(
            "Enrolled (n = 40). 12 weeks later, scores fell.",
            ["Enrolled (n = 40).", "12 weeks later, scores fell."],
            id="sentence opening with a number",
        ),
        pytest.param(
            'Patients said "better." Scores fell? Yes! (Synthetic.) Done',
            ['Patients said "better."', "Scores fell?", "Yes!", "(Synthetic.)", "Done"],
            id="closing quotes and other marks",
        ),
    ],
)
def test_splits_sentences(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


def test_every_gold_sentence_is_a_verbatim_quote() -> None:
    for out in sorted(p for p in GOLD.glob("*/") if (p / "gold.jsonl").exists()):
        for entry in read_gold(out):
            source = normalize_quote(" ".join(s.text for s in entry.abstract.sections))
            for section in entry.abstract.sections:
                sentences = split_sentences(section.text)
                # Nothing is lost or reordered: only the spaces between sentences are dropped.
                assert "".join("".join(sentences).split()) == "".join(section.text.split())
                assert all(normalize_quote(s) in source for s in sentences), entry.abstract.pmid


def test_the_guide_has_a_rule_for_every_abstract_field() -> None:
    rules = field_rules((GOLD / "LABELING.md").read_text())
    fields = {"design", "sample_size", "review_summary", "findings", "quote", "outcome_reported"}
    fields |= {"effect", "is_primary", "is_subgroup", "subgroup", "note"}

    assert fields | {d.name for d in VITAMIN_D.dimensions} <= rules.keys()
    assert rules["effect"].startswith("**`effect`**")
    # The contradiction section's note is not the abstract's.
    assert "reviewer of the label" in rules["note"]


# Saving


def session(tmp_path: Path) -> ReviewSession:
    out = tmp_path / "vitamin-d-depression"
    write_gold(out, gold_entry(), gold_entry(abstract=synthetic_abstract(pmid="999000002")))
    return ReviewSession(out, VITAMIN_D, GOLD / "LABELING.md", tmp_path / "page")


def filled() -> LabelDraft:
    import tomllib

    return LabelDraft.model_validate(tomllib.loads((EXAMPLES / "999000001.toml").read_text()))


def test_an_untouched_template_counts_as_not_started(tmp_path: Path) -> None:
    s = session(tmp_path)
    labels = s.out_dir / "labels"
    labels.mkdir()
    (labels / "999000001.toml").write_text(label_template(gold_entry(), VITAMIN_D))

    statuses = {a.pmid: a.status for a in s.state().abstracts}

    assert statuses == {"999000001": Status.NOT_STARTED, "999000002": Status.NOT_STARTED}


def test_saving_writes_the_file_and_reports_the_check(tmp_path: Path) -> None:
    s = session(tmp_path)
    partial = filled().model_copy(update={"design": ""})

    started = s.save("999000001", partial)
    done = s.save("999000001", filled())

    assert started.status is Status.IN_PROGRESS
    assert any(p.startswith("design:") for p in started.problems)
    assert done.status is Status.LABELED and done.problems == []
    assert done.outcomes == ["synthetic depression score", "synthetic remission"]
    text = (s.out_dir / "labels" / "999000001.toml").read_text()
    assert text.startswith("# Synthetic trial of placeholder supplementation.")
    assert not list((s.out_dir / "labels").glob("*.partial"))


def test_saving_refuses_a_pmid_outside_the_gold_sample(tmp_path: Path) -> None:
    s = session(tmp_path)

    with pytest.raises(KeyError):
        s.save("999000009", filled().model_copy(update={"pmid": "999000009"}))
    with pytest.raises(KeyError):
        s.save("999000002", filled())


def test_a_design_picked_by_name_is_remembered(tmp_path: Path) -> None:
    out = tmp_path / "vitamin-d-depression"
    write_gold(out, gold_entry(), gold_entry(abstract=synthetic_abstract(pmid="999000002")))
    (out / "labels").mkdir()
    by_name = filled().model_copy(update={"design": "cohort", "design_answers": {}})
    answered = filled().model_copy(update={"pmid": "999000002"})
    (out / "labels" / "999000001.toml").write_text(render_label(gold_entry(), VITAMIN_D, by_name))
    (out / "labels" / "999000002.toml").write_text(render_label(gold_entry(), VITAMIN_D, answered))

    marks = ReviewSession(out, VITAMIN_D, GOLD / "LABELING.md", tmp_path / "page").state().marks

    assert marks["999000001"].earlier_design == "cohort"
    assert "999000002" not in marks


def test_marks_stay_in_the_page_directory(tmp_path: Path) -> None:
    s = session(tmp_path)

    s.mark("999000001", Mark(flagged=True, flag_note="Synthetic doubt.", seconds=42))

    assert s.state().marks["999000001"].seconds == 42
    assert s.marks_path.parent == tmp_path / "page"
    assert not (s.out_dir / "labels").exists()


# Requests


def request(
    s: ReviewSession,
    page: Path,
    method: str,
    path: str,
    body: object = None,
    json_type: bool = True,
):
    raw = b"" if body is None else json.dumps(body).encode()
    return handle(s, page, method, path, raw, "application/json" if json_type else "text/plain")


def test_requests(tmp_path: Path) -> None:
    s = session(tmp_path)
    page = write_page(tmp_path / "page")
    label = filled().model_dump(mode="json")

    assert request(s, page, "GET", "/").body.startswith(b"<!doctype html>")
    assert json.loads(request(s, page, "GET", "/api/state").body)["question"] == VITAMIN_D.id
    assert request(s, page, "POST", "/api/label/999000001", label).status == HTTPStatus.OK
    assert (
        request(s, page, "POST", "/api/label/999000001", label, json_type=False).status
        == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    )
    assert (
        request(
            s, page, "POST", "/api/label/999000001", {"pmid": "999000001", "colour": "teal"}
        ).status
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert request(s, page, "POST", "/api/label/999000009", label).status == HTTPStatus.NOT_FOUND
    assert request(s, page, "GET", "/labels/999000001.toml").status == HTTPStatus.NOT_FOUND
