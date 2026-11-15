"""Labeling each outcome string by kind, and the pair verdicts the kinds give.

Every outcome string and pair here is synthetic.
"""

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
from test_labels import ROOT, gold_entry, write_gold
from test_normalize import STRINGS, corpus_results
from test_outcome_pairs import drawn_sample, record_proposal, sample

from claimgraph import outcome_kinds, outcome_pairs
from claimgraph.labels import OutcomePairLabel, PairVerdict
from claimgraph.outcome_kinds import EnteredPair, Kind, KindLabel

EXAMPLES = ROOT / "tests" / "fixtures" / "labels"
QUESTION = "vitamin-d-depression"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Kind.SCORE, Kind.SCORE, PairVerdict.MERGE),
        (Kind.CASE_STATUS, Kind.CASE_STATUS, PairVerdict.MERGE),
        (Kind.REMISSION, Kind.REMISSION, PairVerdict.MERGE),
        (Kind.SCORE, Kind.CASE_STATUS, PairVerdict.SEPARATE),
        (Kind.REMISSION, Kind.RESPONSE, PairVerdict.SEPARATE),
        (Kind.RESPONSE, Kind.SCORE, PairVerdict.SEPARATE),
        (Kind.OUT_OF_DOMAIN, Kind.SCORE, PairVerdict.OUT_OF_DOMAIN),
        (Kind.CASE_STATUS, Kind.OUT_OF_DOMAIN, PairVerdict.OUT_OF_DOMAIN),
        (Kind.OUT_OF_DOMAIN, Kind.OUT_OF_DOMAIN, PairVerdict.OUT_OF_DOMAIN),
    ],
)
def test_a_pair_verdict_follows_from_its_two_kinds(a: Kind, b: Kind, expected: PairVerdict) -> None:
    assert outcome_kinds.verdict(a, b) is expected
    assert outcome_kinds.verdict(b, a) is expected


def test_the_example_kinds_load() -> None:
    labels = outcome_kinds.load(EXAMPLES / "outcome_kinds.toml")

    assert {label.kind for label in labels} == set(Kind)
    assert [label.string for label in labels if label.unsure] == ["synthetic low mood pattern"]


def test_the_guide_names_every_kind() -> None:
    guide = (ROOT / "evals" / "gold" / "LABELING.md").read_text()
    section = guide.split("\n## Outcome kinds\n", 1)[1].split("\n## ", 1)[0]

    assert all(f"`{k}`" in section for k in Kind)


# The label file


def test_the_template_lists_every_string_once_with_no_kind(tmp_path: Path) -> None:
    strings = ["synthetic b score", "synthetic A onset", "synthetic c remission"]

    path = outcome_kinds.write_template(tmp_path, QUESTION, strings)

    entries = tomllib.loads(path.read_text())["outcome"]
    assert [e["string"] for e in entries] == sorted(strings, key=str.casefold)
    assert {e["kind"] for e in entries} == {""}
    assert "LABELING.md" in path.read_text()


def test_the_template_never_overwrites_a_label_file(tmp_path: Path) -> None:
    path = outcome_kinds.write_template(tmp_path, QUESTION, ["synthetic score"])
    path.write_text("# labels in progress\n")

    with pytest.raises(FileExistsError):
        outcome_kinds.write_template(tmp_path, QUESTION, ["synthetic score"])
    assert path.read_text() == "# labels in progress\n"


def test_the_file_keeps_kinds_and_writes_unsure_only_when_set(tmp_path: Path) -> None:
    labels = [
        KindLabel(string="synthetic score", kind=Kind.SCORE),
        KindLabel(string="synthetic onset", kind=Kind.CASE_STATUS, unsure=True),
        KindLabel(string='synthetic "quoted" ≥16'),
    ]
    path = tmp_path / outcome_kinds.LABEL_FILE

    outcome_kinds.write(path, QUESTION, labels)

    assert sorted(outcome_kinds.load(path), key=lambda x: x.string) == sorted(
        labels, key=lambda x: x.string
    )
    assert path.read_text().splitlines().count("unsure = true") == 1


def test_load_refuses_a_string_listed_twice(tmp_path: Path) -> None:
    path = tmp_path / outcome_kinds.LABEL_FILE
    twice = KindLabel(string="synthetic score", kind=Kind.SCORE)
    path.write_text(outcome_kinds.render(QUESTION, [twice, twice]))

    with pytest.raises(ValueError, match="more than once"):
        outcome_kinds.load(path)


def kinds_file(out: Path, labels: list[KindLabel]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    outcome_kinds.write(out / outcome_kinds.LABEL_FILE, QUESTION, labels)


def test_a_blank_file_is_all_unlabeled(tmp_path: Path) -> None:
    outcome_kinds.write_template(tmp_path, QUESTION, ["synthetic a", "synthetic b"])

    report = outcome_kinds.check(tmp_path)

    assert report.drawn is None
    assert report.problems == ["2 of 2 strings have no kind yet"]


def test_the_check_counts_each_kind(tmp_path: Path) -> None:
    kinds_file(
        tmp_path,
        [
            KindLabel(string="synthetic a", kind=Kind.SCORE),
            KindLabel(string="synthetic b", kind=Kind.SCORE, unsure=True),
            KindLabel(string="synthetic c", kind=Kind.OUT_OF_DOMAIN),
        ],
    )

    report = outcome_kinds.check(tmp_path)

    assert report.problems == []
    assert report.count(Kind.SCORE) == 2
    assert report.count(Kind.OUT_OF_DOMAIN) == 1
    assert report.count(Kind.RESPONSE) == 0


def test_a_kind_outside_the_five_is_named_by_string(tmp_path: Path) -> None:
    (tmp_path / outcome_kinds.LABEL_FILE).write_text(
        '[[outcome]]\nstring = "synthetic a"\nkind = "score"\n\n'
        '[[outcome]]\nstring = "synthetic b"\nkind = "severity"\n'
    )

    report = outcome_kinds.check(tmp_path)

    assert len(report.labels) == 1
    assert report.problems and all(p.startswith("string 2: kind") for p in report.problems)


def test_a_string_listed_twice_is_named(tmp_path: Path) -> None:
    (tmp_path / outcome_kinds.LABEL_FILE).write_text(
        '[[outcome]]\nstring = "synthetic a"\nkind = "score"\n\n'
        '[[outcome]]\nstring = "synthetic a"\nkind = "response"\n'
    )

    report = outcome_kinds.check(tmp_path)

    assert report.problems == ['string 2: "synthetic a" is listed more than once']


def test_a_single_bracket_table_is_named_not_walked(tmp_path: Path) -> None:
    (tmp_path / outcome_kinds.LABEL_FILE).write_text(
        '[outcome]\nstring = "synthetic a"\nkind = "score"\n'
    )

    report = outcome_kinds.check(tmp_path)

    assert report.problems == ["strings are written [[outcome]], one table each"]


def test_the_file_must_hold_every_string_the_pairs_were_drawn_from(tmp_path: Path) -> None:
    # The synthetic draw names 16 strings; this file lists one of them.
    out = drawn_sample(tmp_path)
    drawn = outcome_pairs.load_sample(out / outcome_pairs.SAMPLE_FILE)
    first = drawn.pairs[0].a
    kinds_file(out, [KindLabel(string=first, kind=Kind.SCORE)])

    report = outcome_kinds.check(out)

    assert report.drawn == 16
    assert "the draw has 16 strings; the file lists 1" in report.problems
    assert any("strings in drawn pairs are not in the file" in p for p in report.problems)


# Pair verdicts entered by hand


PAIRS = """# Synthetic pair verdicts, entered by hand.

[[pair]]
a = "synthetic score"
b = "synthetic symptoms"
verdict = "merge"

[[pair]]
a = "synthetic score"
b = "synthetic onset"
verdict = "merge" # unsure: no cut-off given

[[pair]]
a = "synthetic onset"
b = "synthetic symptoms"
verdict = merge"

[[pair]]
a = "synthetic score"
b = "synthetic remission"
verdict = ""

[[pair]]
a = "synthetic remission"
b = "synthetic new string"
verdict = "separate"

[[pair]]
a = "synthetic distress"
b = "synthetic onset"
verdict = "separate"
note = "Read distress as a depression score."
"""


def test_entered_pairs_are_read_one_table_at_a_time(tmp_path: Path) -> None:
    path = tmp_path / "outcome_pairs.toml"
    path.write_text(PAIRS)

    entered = outcome_kinds.read_entered_pairs(path)

    assert [p.label.verdict for p in entered.pairs] == ["merge", "merge", "separate", "separate"]
    assert entered.blank == 1
    assert len(entered.problems) == 1
    assert entered.problems[0].startswith("pair 3 (line 13): not read:")
    assert [p.comment for p in entered.pairs] == [
        None,
        "unsure: no cut-off given",
        None,
        "Read distress as a depression score.",
    ]


def entered(a: str, b: str, verdict: str, comment: str | None = None) -> EnteredPair:
    label = OutcomePairLabel.model_validate({"a": a, "b": b, "verdict": verdict})
    return EnteredPair(label=label, comment=comment)


KINDS = [
    KindLabel(string="synthetic score", kind=Kind.SCORE),
    KindLabel(string="synthetic symptoms", kind=Kind.SCORE),
    KindLabel(string="synthetic onset", kind=Kind.CASE_STATUS, unsure=True),
    KindLabel(string="synthetic remission", kind=Kind.REMISSION),
    KindLabel(string="synthetic distress", kind=Kind.OUT_OF_DOMAIN),
    KindLabel(string="synthetic new string"),
]


def test_the_comparison_lists_each_verdict_the_kinds_do_not_give() -> None:
    result = outcome_kinds.compare(
        [
            entered("synthetic symptoms", "synthetic score", "merge"),
            entered("synthetic score", "synthetic onset", "merge", "unsure: no cut-off given"),
            entered("synthetic distress", "synthetic onset", "separate"),
            entered("synthetic remission", "synthetic new string", "separate"),
            entered("synthetic remission", "synthetic absent", "separate"),
        ],
        KINDS,
    )

    assert result.agree == 1
    assert result.waiting == 2
    assert [(d.a, d.b, d.entered, d.derived) for d in result.disagree] == [
        ("synthetic onset", "synthetic score", "merge", "separate"),
        ("synthetic distress", "synthetic onset", "separate", "out_of_domain"),
    ]
    first, second = result.disagree
    assert (first.kind_a, first.kind_b) == (Kind.CASE_STATUS, Kind.SCORE)
    assert first.unsure
    assert first.comment == "unsure: no cut-off given"
    assert second.unsure


# The commands


def run_kinds(tmp_path: Path, command: str, *args: str) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    config = str(ROOT / "questions" / f"{QUESTION}.toml")
    return CliRunner().invoke(
        cli.app,
        ["gold", "outcome-kinds", command, config, "--gold-dir", str(tmp_path / "gold"), *args],
    )


def test_the_template_command_lists_the_strings_of_the_recorded_proposal(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    record_proposal(tmp_path)
    assert sample(tmp_path).exit_code == 0

    result = run_kinds(tmp_path, "template", "--runs-dir", str(tmp_path / "runs"))

    assert result.exit_code == 0, result.output
    path = tmp_path / "gold" / QUESTION / outcome_kinds.LABEL_FILE
    assert sorted(label.string for label in outcome_kinds.load(path)) == sorted(STRINGS)
    assert "3 strings" in result.output


def test_the_template_command_needs_the_strings_the_pairs_were_drawn_from(
    tmp_path: Path,
) -> None:
    # The synthetic draw names a proposal no corpus string list makes.
    corpus_results(tmp_path)
    out = drawn_sample(tmp_path)

    result = run_kinds(tmp_path, "template", "--runs-dir", str(tmp_path / "runs"))

    assert result.exit_code == 1
    assert "not the ones the recorded proposal" in result.output
    assert not (out / outcome_kinds.LABEL_FILE).exists()


def test_the_template_command_needs_a_draw(tmp_path: Path) -> None:
    corpus_results(tmp_path)

    result = run_kinds(tmp_path, "template", "--runs-dir", str(tmp_path / "runs"))

    assert result.exit_code == 1
    assert "gold outcome-pairs sample" in result.output


def test_the_check_command_reports_progress_and_fails_until_done(tmp_path: Path) -> None:
    out = tmp_path / "gold" / QUESTION
    kinds_file(out, KINDS)

    result = run_kinds(tmp_path, "check")

    assert result.exit_code == 1
    assert (
        "5 of 6 strings labeled: 2 score, 1 case_status, 1 remission, 0 response, "
        "1 out_of_domain; 1 unsure"
    ) in result.output
    assert "1 of 6 strings have no kind yet" in result.output

    kinds_file(out, [k for k in KINDS if k.kind])
    assert run_kinds(tmp_path, "check").exit_code == 0


def test_gold_check_fails_on_an_unfinished_kind_file(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from claimgraph import cli

    out = tmp_path / "gold" / QUESTION
    write_gold(out, gold_entry())
    kinds_file(out, KINDS)

    result = CliRunner().invoke(
        cli.app,
        [
            "gold",
            "check",
            str(ROOT / "questions" / f"{QUESTION}.toml"),
            "--gold-dir",
            str(tmp_path / "gold"),
        ],
    )

    assert result.exit_code == 1
    assert "outcome_kinds.toml: 1 of 6 strings have no kind yet" in result.output


def test_the_compare_command_lists_disagreements(tmp_path: Path) -> None:
    out = tmp_path / "gold" / QUESTION
    kinds_file(out, KINDS)
    (out / "outcome_pairs.toml").write_text(PAIRS)

    result = run_kinds(tmp_path, "compare")

    assert result.exit_code == 0, result.output
    assert (
        "4 pair verdicts entered and 1 left blank. Against the kinds: 1 agree, 2 disagree, "
        "1 wait on a string with no kind."
    ) in result.output.replace("\n", " ")
    assert (
        "- synthetic onset / synthetic score: entered merge, kinds give separate "
        "(case_status / score); a kind is marked unsure; your comment: unsure: no cut-off given"
    ) in result.output
    assert "pair 3 (line 13): not read" in result.output


def test_the_compare_command_needs_the_kind_file(tmp_path: Path) -> None:
    out = tmp_path / "gold" / QUESTION
    out.mkdir(parents=True)
    (out / "outcome_pairs.toml").write_text(PAIRS)

    result = run_kinds(tmp_path, "compare")

    assert result.exit_code == 1
    assert "not compared" in result.output


def test_committed_outcome_kinds_are_complete() -> None:
    # Kinds in progress sit uncommitted; once committed, every string must have a kind.
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "evals/gold"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    for name in listed.split("\0"):
        if name.endswith("/" + outcome_kinds.LABEL_FILE):
            out = (ROOT / name).parent
            assert outcome_kinds.check(out).problems == [], out.name


# Corrections made after labeling


CORRECTIONS = """
[[correction]]
string = "synthetic score"
was = "case_status"
now = "score"
found = "pair_verdicts"
rule = "A synthetic rule."

[[correction]]
string = "synthetic onset"
was = "score"
now = "case_status"
found = "first_proposal"
rule = "Another synthetic rule."
"""


def test_corrections_restore_the_labels_they_replaced(tmp_path: Path) -> None:
    path = tmp_path / outcome_kinds.CORRECTIONS_FILE
    path.write_text(CORRECTIONS)
    corrections = outcome_kinds.load_corrections(path)

    before = outcome_kinds.before(KINDS, corrections, {outcome_kinds.Found.FIRST_PROPOSAL})

    kinds = {label.string: label.kind for label in before}
    assert kinds["synthetic onset"] == Kind.SCORE
    assert kinds["synthetic score"] == Kind.SCORE
    assert len(before) == len(KINDS)


def test_a_correction_must_match_the_current_label(tmp_path: Path) -> None:
    path = tmp_path / outcome_kinds.CORRECTIONS_FILE
    path.write_text(CORRECTIONS.replace('now = "case_status"', 'now = "remission"'))

    with pytest.raises(ValueError, match="synthetic onset"):
        outcome_kinds.before(
            KINDS, outcome_kinds.load_corrections(path), {outcome_kinds.Found.FIRST_PROPOSAL}
        )


def test_the_gold_readme_lists_every_outcome_correction() -> None:
    out = ROOT / "evals" / "gold" / "vitamin-d-depression"
    corrections = outcome_kinds.load_corrections(out / outcome_kinds.CORRECTIONS_FILE)
    readme = (ROOT / "evals" / "gold" / "README.md").read_text()
    labels = {
        label.string: label.kind for label in outcome_kinds.load(out / outcome_kinds.LABEL_FILE)
    }

    rows = outcome_kinds.correction_rows(corrections)
    assert all(row in readme for row in rows)
    table = readme.split("| String | Was | Now | Rule | Found |", 1)[1].split("\n\n", 1)[0]
    assert len([line for line in table.splitlines() if line.startswith("| ")]) == len(rows)
    assert all(labels[c.string] == c.now for c in corrections)
