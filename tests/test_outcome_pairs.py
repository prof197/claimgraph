"""Drawing the outcome pairs for the normalization eval.

Every outcome string, acronym and proposal here is synthetic.
"""

import json
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest
from test_extract import Scripted, response
from test_labels import ROOT, VITAMIN_D
from test_normalize import STRINGS, VALID, corpus_results

from claimgraph import normalize, outcome_pairs
from claimgraph.llm import Mode, ModelClient, request_key
from claimgraph.outcome_pairs import Stratum, features, stratum

# Every test string carries "synthetic", so the tests treat it as a common word, as the draw
# treats a word that most of the corpus's strings share.
COMMON = frozenset({"synthetic"})


def test_acronyms_ignore_versions_and_hyphens() -> None:
    assert outcome_pairs.acronyms("synthetic SMS-9 score") == {"SMS"}
    assert outcome_pairs.acronyms("synthetic SMI-II") == outcome_pairs.acronyms("synthetic SMI")
    assert outcome_pairs.acronyms("synthetic SM-D total") == outcome_pairs.acronyms("synthetic SMD")
    assert outcome_pairs.acronyms("synthetic SM-D17") == outcome_pairs.acronyms("synthetic SMD")
    assert outcome_pairs.acronyms("synthetic SMI-Ia") == outcome_pairs.acronyms("synthetic SMI")
    assert outcome_pairs.acronyms("synthetic mood and 25(OH)D") == set()
    assert outcome_pairs.acronyms("synthetic low mood") == set()


@pytest.mark.parametrize(
    "text",
    [
        "synthetic SMS ≥16",
        "synthetic SMS score >= 17",
        "synthetic SMS <11",
        "synthetic SMS score of 13 or more",
        "synthetic SMS cut-off of 7",
        "clinically relevant synthetic low mood",
    ],
)
def test_a_cut_off_is_a_threshold_on_a_scale(text: str) -> None:
    assert outcome_pairs.has_cut_off(text)


@pytest.mark.parametrize(
    "text",
    [
        "synthetic SMS score",
        "synthetic SMS scores at 12-20 weeks",
        "synthetic low mood at 2-year follow-up",
        "synthetic low mood (SMS-9)",
    ],
)
def test_a_score_or_a_timing_is_not_a_cut_off(text: str) -> None:
    assert not outcome_pairs.has_cut_off(text)


def test_common_words_are_those_in_more_than_a_tenth_of_the_strings() -> None:
    strings = [f"synthetic mood item {n}" for n in range(9)] + ["synthetic winter mood"]

    assert outcome_pairs.common_words(strings) == ["item", "mood", "synthetic"]


def classify(a: str, b: str, grouped: bool = False, named: bool = False) -> Stratum:
    return stratum(features(a, COMMON), features(b, COMMON), grouped=grouped, named=named)


@pytest.mark.parametrize(
    ("a", "b", "grouped", "expected"),
    [
        ("synthetic SMS ≥16", "synthetic SMS score", True, Stratum.CUT_OFF_VS_SCORE),
        ("synthetic SMS ≥16", "synthetic low mood diagnosis", True, Stratum.CUT_OFF_VS_CASE),
        (
            "incident synthetic low mood",
            "synthetic low mood severity",
            False,
            Stratum.INCIDENCE_VS_SEVERITY,
        ),
        ("synthetic low mood", "synthetic SMS", True, Stratum.GENERAL_VS_SCALE),
        ("synthetic SMS score", "synthetic SMI score", True, Stratum.GROUPED),
        ("synthetic SMS total", "synthetic SMS anxiety items", False, Stratum.SPLIT_RELATED),
        ("synthetic winter mood", "synthetic winter sleep", False, Stratum.SPLIT_RELATED),
        ("synthetic low mood", "synthetic sleep quality", False, Stratum.SPLIT_UNRELATED),
    ],
)
def test_each_pair_falls_in_the_first_stratum_it_fits(
    a: str, b: str, grouped: bool, expected: Stratum
) -> None:
    assert classify(a, b, grouped) is expected
    assert classify(b, a, grouped) is expected


def test_two_named_hard_cases_are_named_whatever_else_they_fit() -> None:
    assert classify("synthetic SMS ≥16", "synthetic SMS score", named=True) is Stratum.NAMED


# A synthetic corpus: two proposed outcomes, with cut-offs, case terms and shared words.
SEVERITY = [
    "synthetic low mood",
    "synthetic SMS score",
    "synthetic SMS total at 8 weeks",
    "synthetic SMI score",
    "synthetic SMI-II",
    "synthetic winter low mood",
    "synthetic low mood severity",
    "synthetic RMS score",
]
CASE = [
    "synthetic SMS ≥16",
    "synthetic SMI ≥10",
    "incident synthetic low mood",
    "synthetic low mood diagnosis",
    "synthetic winter low mood onset",
    "synthetic low mood prevalence",
]
OTHER = ["synthetic sleep quality", "synthetic RMS anxiety items"]
GROUPS = {s: "severity" for s in SEVERITY} | {s: "case" for s in CASE} | {s: "other" for s in OTHER}
CORPUS = sorted(GROUPS)
HARD = ["Synthetic low mood", "synthetic SMS score", "synthetic absent string"]
SMALL = {
    Stratum.CUT_OFF_VS_SCORE: 2,
    Stratum.CUT_OFF_VS_CASE: 2,
    Stratum.INCIDENCE_VS_SEVERITY: 2,
    Stratum.GENERAL_VS_SCALE: 2,
    Stratum.GROUPED: 3,
    Stratum.SPLIT_RELATED: 3,
}


def draw(seed: int = 1) -> outcome_pairs.Draw:
    return outcome_pairs.draw(CORPUS, GROUPS, HARD, seed=seed, quotas=SMALL)


def test_the_pools_cover_every_pair_once() -> None:
    result = draw()

    assert sum(result.pools.values()) == len(list(combinations(CORPUS, 2)))


def test_the_draw_takes_each_quota_or_the_whole_pool_if_smaller() -> None:
    result = draw()

    drawn = {s: sum(p.stratum is s for p in result.pairs) for s in Stratum}
    for s, quota in SMALL.items():
        assert drawn[s] == min(quota, result.pools[s])
    assert drawn[Stratum.SPLIT_UNRELATED] == 0


def test_every_pair_of_named_hard_cases_present_is_drawn() -> None:
    result = draw()

    named = [(p.a, p.b) for p in result.pairs if p.stratum is Stratum.NAMED]
    assert named == [("synthetic SMS score", "synthetic low mood")]


def test_drawn_pairs_are_distinct_and_in_sorted_order() -> None:
    pairs = [(p.a, p.b) for p in draw().pairs]

    assert len(set(pairs)) == len(pairs)
    assert all(a < b for a, b in pairs)


def test_grouped_pairs_share_a_proposed_outcome_and_split_pairs_do_not() -> None:
    for p in draw().pairs:
        if p.stratum is Stratum.GROUPED:
            assert GROUPS[p.a] == GROUPS[p.b]
        if p.stratum is Stratum.SPLIT_RELATED:
            assert GROUPS[p.a] != GROUPS[p.b]


def test_one_seed_draws_the_same_pairs() -> None:
    assert draw(seed=7) == draw(seed=7)


# The command


def record_proposal(tmp_path: Path) -> str:
    replay = tmp_path / "replay" / "vitamin-d-depression" / "normalization" / "sonnet"
    client = ModelClient(replay, Mode.LIVE, transport=Scripted(response(json.dumps(VALID))))
    proposal = normalize.propose(sorted(STRINGS), VITAMIN_D, client)
    assert proposal.outcomes is not None
    return request_key(normalize.request(sorted(STRINGS), VITAMIN_D))


def run_pairs(tmp_path: Path, command: str, *args: str) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    config = str(ROOT / "questions" / "vitamin-d-depression.toml")
    return CliRunner().invoke(
        cli.app,
        [
            "gold",
            "outcome-pairs",
            command,
            config,
            "--gold-dir",
            str(tmp_path / "gold"),
            *args,
        ],
    )


def sample(tmp_path: Path) -> Any:
    return run_pairs(
        tmp_path,
        "sample",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--replay-dir",
        str(tmp_path / "replay"),
    )


def test_the_sample_draws_from_the_recorded_proposal(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    key = record_proposal(tmp_path)

    result = sample(tmp_path)

    assert result.exit_code == 0, result.output
    path = tmp_path / "gold" / "vitamin-d-depression" / "outcome_pair_sample.json"
    drawn = outcome_pairs.load_sample(path)
    assert drawn.proposal == key
    assert drawn.model == "claude-sonnet-5-5"
    assert drawn.strings == 3
    # With three strings every word is common, so the pair sharing no outcome and no case or
    # severity term is unrelated and not drawn.
    assert [p.stratum for p in drawn.pairs] == [Stratum.INCIDENCE_VS_SEVERITY, Stratum.GROUPED]
    assert "2 pairs drawn from 3 outcome strings" in result.output


def test_the_sample_needs_a_recorded_proposal(tmp_path: Path) -> None:
    corpus_results(tmp_path)

    result = sample(tmp_path)

    assert result.exit_code == 1
    assert "no recorded outcome proposal" in result.output


def test_the_sample_is_never_redrawn_over_an_existing_one(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    record_proposal(tmp_path)
    assert sample(tmp_path).exit_code == 0
    path = tmp_path / "gold" / "vitamin-d-depression" / "outcome_pair_sample.json"
    before = path.read_text()

    result = sample(tmp_path)

    assert result.exit_code == 1
    assert "already drawn" in result.output
    assert path.read_text() == before
