"""The local page for labeling outcome strings by kind.

Every outcome string here is synthetic.
"""

import json
import threading
from http import HTTPStatus
from pathlib import Path

import pytest
from test_labels import ROOT

from claimgraph import kind_review, outcome_kinds
from claimgraph.kind_review import Edit, KindSession
from claimgraph.outcome_kinds import Kind, KindLabel
from claimgraph.review import Response

GUIDE = ROOT / "evals" / "gold" / "LABELING.md"
STRINGS = [f"synthetic outcome {n:02}" for n in range(30)]


def session(tmp_path: Path, strings: list[str] = STRINGS) -> KindSession:
    outcome_kinds.write_template(tmp_path, "synthetic-question", strings)
    return KindSession(tmp_path, "synthetic-question", "Synthetic question", GUIDE)


def test_the_rules_are_the_guide_s_outcome_section() -> None:
    rules = kind_review.guide_section(GUIDE.read_text())

    assert rules.startswith("Each reported outcome string gets one kind")
    assert "### Out of domain" in rules
    assert "### Polarity" in rules
    assert "## Contradiction pairs" not in rules
    assert "\n## " not in rules


def test_a_missing_section_is_named() -> None:
    with pytest.raises(ValueError, match="Outcome kinds"):
        kind_review.guide_section("# Guide\n\n## Abstracts\n\nText.\n")


def test_the_page_lists_every_string_once_in_one_shuffled_order(tmp_path: Path) -> None:
    page = session(tmp_path)

    first = [s.string for s in page.state().strings]
    again = [s.string for s in page.state().strings]

    assert sorted(first) == STRINGS
    assert first != STRINGS
    assert first == again


def test_the_page_shows_each_string_alone(tmp_path: Path) -> None:
    # The state carries the string, its kind and the unsure flag, and nothing about grouping.
    state = json.loads(session(tmp_path).state().model_dump_json())

    assert set(state) == {"question", "title", "kinds", "rules", "strings"}
    assert {frozenset(s) for s in state["strings"]} == {frozenset({"string", "kind", "unsure"})}
    assert state["kinds"] == [k.value for k in Kind]


def test_a_save_writes_the_file_and_keeps_every_other_string(tmp_path: Path) -> None:
    page = session(tmp_path)

    saved = page.save(Edit(string=STRINGS[3], kind=Kind.RESPONSE, unsure=True))

    labels = {label.string: label for label in outcome_kinds.load(page.path)}
    assert labels[STRINGS[3]] == KindLabel(string=STRINGS[3], kind=Kind.RESPONSE, unsure=True)
    assert all(labels[s] == KindLabel(string=s) for s in STRINGS if s != STRINGS[3])
    assert (saved.labeled, saved.unsure, saved.total) == (1, 1, 30)


def test_a_blank_kind_clears_the_label(tmp_path: Path) -> None:
    page = session(tmp_path)
    page.save(Edit(string=STRINGS[0], kind=Kind.SCORE, unsure=False))

    saved = page.save(Edit(string=STRINGS[0], kind="", unsure=False))

    assert saved.labeled == 0
    assert outcome_kinds.load(page.path)[0] == KindLabel(string=STRINGS[0])


def test_saves_at_once_all_land(tmp_path: Path) -> None:
    page = session(tmp_path)
    threads = [
        threading.Thread(target=page.save, args=(Edit(string=s, kind=Kind.SCORE, unsure=False),))
        for s in STRINGS
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert {label.kind for label in outcome_kinds.load(page.path)} == {Kind.SCORE}


def post(page: KindSession, body: object, content_type: str = "application/json") -> Response:
    return kind_review.handle(page, "POST", "/api/kind", json.dumps(body).encode(), content_type)


def test_requests(tmp_path: Path) -> None:
    page = session(tmp_path)

    html = kind_review.handle(page, "GET", "/", b"", "")
    state = kind_review.handle(page, "GET", "/api/state", b"", "")
    saved = post(page, {"string": STRINGS[1], "kind": "case_status", "unsure": False})
    form = post(page, {"string": STRINGS[1], "kind": "score", "unsure": False}, "text/plain")
    unknown = post(page, {"string": "synthetic absent", "kind": "score", "unsure": False})
    invalid = post(page, {"string": STRINGS[1], "kind": "severity", "unsure": False})
    missing = kind_review.handle(page, "GET", "/api/mapping", b"", "")

    assert html.status == HTTPStatus.OK
    assert b"<title>Outcome kinds</title>" in html.body
    assert state.status == HTTPStatus.OK
    assert saved.status == HTTPStatus.OK
    assert form.status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert unknown.status == HTTPStatus.NOT_FOUND
    assert invalid.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert missing.status == HTTPStatus.NOT_FOUND
    labels = {label.string: label.kind for label in outcome_kinds.load(page.path)}
    assert labels[STRINGS[1]] == Kind.CASE_STATUS


def test_a_file_broken_by_hand_is_never_rewritten(tmp_path: Path) -> None:
    page = session(tmp_path)
    broken = page.path.read_text().replace('kind = ""', 'kind = "severity"', 1)
    page.path.write_text(broken)

    state = kind_review.handle(page, "GET", "/api/state", b"", "")
    saved = post(page, {"string": STRINGS[5], "kind": "score", "unsure": False})

    assert state.status == HTTPStatus.CONFLICT
    assert saved.status == HTTPStatus.CONFLICT
    assert b"Fix it by hand" in state.body
    assert page.path.read_text() == broken


def test_the_review_command_needs_a_kind_file(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from claimgraph import cli

    result = CliRunner().invoke(
        cli.app,
        [
            "gold",
            "outcome-kinds",
            "review",
            str(ROOT / "questions" / "vitamin-d-depression.toml"),
            "--gold-dir",
            str(tmp_path),
            "--no-open",
        ],
    )

    assert result.exit_code == 1
    assert "run gold outcome-kinds template first" in result.output


def test_rows_a_filter_hides_are_not_displayed() -> None:
    # Rows are display: flex, which beats the browser's own rule for the hidden attribute.
    assert ".row[hidden] { display: none; }" in kind_review.page().decode()
