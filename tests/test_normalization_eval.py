"""Scoring the outcome proposal against the outcome kinds.

Every outcome string, outcome and proposal here is synthetic.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from test_extract import Scripted, response
from test_labels import ROOT, VITAMIN_D
from test_normalize import STRINGS, assign, corpus_results, outcome
from test_outcome_pairs import record_proposal, sample

from claimgraph import normalization_eval, normalize, outcome_kinds
from claimgraph.llm import Mode, ModelClient
from claimgraph.models import NormalizedOutcome
from claimgraph.outcome_kinds import Kind, KindLabel
from claimgraph.outcome_pairs import DrawnPair, PairSample, Stratum

KINDS = {
    "synthetic score a": Kind.SCORE,
    "synthetic score b": Kind.SCORE,
    "synthetic score c": Kind.SCORE,
    "synthetic case a": Kind.CASE_STATUS,
    "synthetic case b": Kind.CASE_STATUS,
    "synthetic remission": Kind.REMISSION,
    "synthetic distress": Kind.OUT_OF_DOMAIN,
}
LABELS = [KindLabel(string=s, kind=k) for s, k in KINDS.items()]


def grouping(*groups: tuple[str, list[str]]) -> list[NormalizedOutcome]:
    return [
        NormalizedOutcome(name=name, members=members, higher_is_better=False, reviewed=False)
        for name, members in groups
    ]


# The proposal merges two of the three scores with a case string, keeps the third score apart,
# and puts the remission string outside the domain with the distress string.
PROPOSED = grouping(
    ("synthetic severity", ["synthetic score a", "synthetic score b", "synthetic case a"]),
    ("synthetic score c", ["synthetic score c"]),
    ("synthetic case", ["synthetic case b"]),
    ("synthetic other", ["synthetic remission", "synthetic distress"]),
)


def drawn(*pairs: tuple[str, str, Stratum]) -> PairSample:
    return PairSample(
        question="synthetic-question",
        seed=1,
        model="synthetic",
        proposal="0" * 64,
        strings=len(KINDS),
        common_words=[],
        pools={s: 1 for s in Stratum},
        drawn={s: sum(p[2] is s for p in pairs) for s in Stratum},
        pairs=[DrawnPair(a=a, b=b, stratum=s) for a, b, s in pairs],
    )


DRAWN = drawn(
    ("synthetic score a", "synthetic score b", Stratum.GROUPED),
    ("synthetic case a", "synthetic score a", Stratum.GROUPED),
    ("synthetic score a", "synthetic score c", Stratum.SPLIT_RELATED),
    ("synthetic distress", "synthetic score c", Stratum.SPLIT_RELATED),
)


def test_every_pair_of_in_domain_strings_is_scored() -> None:
    scores = normalization_eval.score(LABELS, PROPOSED, ["synthetic other"], DRAWN)

    # Six in-domain strings: three score pairs and one case pair are labeled merges; the
    # proposal merges three pairs inside "synthetic severity", one of them a score pair.
    counts = scores.all_pairs
    assert (counts.pairs, counts.labeled, counts.proposed, counts.both) == (15, 4, 3, 1)
    assert counts.precision == pytest.approx(1 / 3)
    assert counts.recall == pytest.approx(1 / 4)


def test_an_outcome_outside_the_domain_merges_nothing() -> None:
    together = grouping(
        ("synthetic all", ["synthetic score a", "synthetic score b", "synthetic score c"]),
        ("synthetic case", ["synthetic case a", "synthetic case b"]),
        ("synthetic other", ["synthetic remission", "synthetic distress"]),
    )

    inside = normalization_eval.score(LABELS, together, [], DRAWN)
    outside = normalization_eval.score(LABELS, together, ["synthetic all"], DRAWN)

    assert inside.all_pairs.both == 4
    assert outside.all_pairs.both == 1
    assert outside.proposed_out == 3
    assert outside.both_out == 0


def test_strings_labeled_out_of_domain_are_counted_not_scored() -> None:
    scores = normalization_eval.score(LABELS, PROPOSED, ["synthetic other"], DRAWN)

    assert (scores.labeled_out, scores.proposed_out, scores.both_out) == (1, 2, 1)
    split = next(s for s in scores.strata if s.stratum is Stratum.SPLIT_RELATED)
    assert (split.drawn, split.out_of_domain, split.counts.pairs) == (2, 1, 1)
    assert (split.counts.labeled, split.counts.proposed) == (1, 0)


def test_the_drawn_pairs_are_scored_stratum_by_stratum() -> None:
    scores = normalization_eval.score(LABELS, PROPOSED, ["synthetic other"], DRAWN)

    grouped = next(s for s in scores.strata if s.stratum is Stratum.GROUPED)
    assert (grouped.counts.labeled, grouped.counts.proposed, grouped.counts.both) == (1, 2, 1)
    assert (scores.drawn.pairs, scores.drawn.labeled, scores.drawn.both) == (3, 2, 1)


def test_every_string_needs_a_kind() -> None:
    blank = [*LABELS[:-1], KindLabel(string="synthetic distress")]

    with pytest.raises(ValueError, match="no kind yet"):
        normalization_eval.score(blank, PROPOSED, [], DRAWN)


def test_the_proposal_and_the_labels_must_list_the_same_strings() -> None:
    with pytest.raises(ValueError, match="different strings"):
        normalization_eval.score(LABELS[:-1], PROPOSED, [], DRAWN)


def test_the_table_shows_rates_and_marks_an_empty_one() -> None:
    text = normalization_eval.table(
        normalization_eval.score(LABELS, PROPOSED, ["synthetic other"], DRAWN)
    )

    assert "| Every pair of in-domain strings | 15 | 4 | 3 | 1 | 33.3% | 25.0% |" in text
    assert "| split related | 1 | 2 | 1 | 1 | 1 | 0 | 0 | n/a | 0.0% |" in text
    assert "7 strings, 1 labeled out of domain" in text


def test_the_proposal_names_the_outcomes_it_put_outside_the_domain(tmp_path: Path) -> None:
    reply = {
        "outcomes": [
            outcome("synthetic mood symptoms"),
            outcome("synthetic sleep", "outside the domain"),
        ],
        "assignments": assign(
            (1, "synthetic mood symptoms"), (2, "synthetic sleep"), (3, "synthetic mood symptoms")
        ),
    }
    client = ModelClient(tmp_path, Mode.LIVE, transport=Scripted(response(json.dumps(reply))))

    proposal = normalize.propose(sorted(STRINGS), VITAMIN_D, client)

    assert proposal.outside == ["synthetic sleep"]


# The command


def run_eval(tmp_path: Path, *args: str) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    return CliRunner().invoke(
        cli.app,
        [
            "eval",
            "normalization",
            str(ROOT / "questions" / "vitamin-d-depression.toml"),
            "--gold-dir",
            str(tmp_path / "gold"),
            "--replay-dir",
            str(tmp_path / "replay"),
            *args,
        ],
    )


def recorded(tmp_path: Path) -> Path:
    """A synthetic corpus, its recorded proposal and its draw."""
    corpus_results(tmp_path)
    record_proposal(tmp_path)
    assert sample(tmp_path).exit_code == 0
    return tmp_path / "gold" / "vitamin-d-depression"


def test_the_command_scores_the_recorded_proposal(tmp_path: Path) -> None:
    # VALID groups the low mood and the mood score, and keeps the onset apart.
    out = recorded(tmp_path)
    kinds = {
        "synthetic low mood": Kind.SCORE,
        "synthetic mood score": Kind.SCORE,
        "synthetic mood disorder onset": Kind.CASE_STATUS,
    }
    outcome_kinds.write(
        out / outcome_kinds.LABEL_FILE,
        "vitamin-d-depression",
        [KindLabel(string=s, kind=k) for s, k in kinds.items()],
    )

    result = run_eval(tmp_path)

    assert result.exit_code == 0, result.output
    assert "| Every pair of in-domain strings | 3 | 1 | 1 | 1 | 100.0% | 100.0% |" in result.output


def test_the_command_needs_the_recording(tmp_path: Path) -> None:
    out = recorded(tmp_path)
    outcome_kinds.write(
        out / outcome_kinds.LABEL_FILE,
        "vitamin-d-depression",
        [KindLabel(string=s, kind=Kind.SCORE) for s in STRINGS],
    )
    for path in (tmp_path / "replay").rglob("*.json"):
        path.unlink()

    result = run_eval(tmp_path)

    assert result.exit_code == 1
    assert "no recorded proposal" in result.output


def test_the_command_needs_the_strings_the_pairs_were_drawn_from(tmp_path: Path) -> None:
    out = recorded(tmp_path)
    outcome_kinds.write(
        out / outcome_kinds.LABEL_FILE,
        "vitamin-d-depression",
        [KindLabel(string=s, kind=Kind.SCORE) for s in [*STRINGS, "synthetic extra"]],
    )

    result = run_eval(tmp_path)

    assert result.exit_code == 1
    assert "the draw has 3 strings; the file lists 4" in result.output


def test_the_vitamin_d_proposal_scores_from_committed_files() -> None:
    # CI has no run outputs; the kind file and the recording are enough.
    from typer.testing import CliRunner

    from claimgraph import cli

    result = CliRunner().invoke(
        cli.app, ["eval", "normalization", str(ROOT / "questions" / "vitamin-d-depression.toml")]
    )

    assert result.exit_code == 0, result.output
    assert "Every pair of in-domain strings" in result.output


def test_the_baseline_is_the_recording_the_pairs_were_drawn_from(tmp_path: Path) -> None:
    out = recorded(tmp_path)
    outcome_kinds.write(
        out / outcome_kinds.LABEL_FILE,
        "vitamin-d-depression",
        [KindLabel(string=s, kind=Kind.SCORE) for s in STRINGS],
    )

    result = run_eval(tmp_path, "--baseline")

    # VALID merges two of the three strings, which are all labeled scores.
    assert result.exit_code == 0, result.output
    assert "| Every pair of in-domain strings | 3 | 3 | 1 | 1 | 100.0% | 33.3% |" in result.output


def test_the_vitamin_d_baseline_scores_from_committed_files() -> None:
    from typer.testing import CliRunner

    from claimgraph import cli

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "normalization",
            str(ROOT / "questions" / "vitamin-d-depression.toml"),
            "--baseline",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Every pair of in-domain strings" in result.output
