from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from claimgraph import contradictions
from claimgraph.cli import app
from claimgraph.compare import Candidates
from claimgraph.models import (
    Arm,
    CandidatePair,
    ClassifiedPairs,
    PairClassification,
    PairKind,
    Reason,
    Verdict,
)


def candidates(n: int) -> Candidates:
    pairs = [
        CandidatePair(
            outcome="synthetic symptoms",
            study_a=f"999{i:06d}",
            study_b=f"999{i + n:06d}",
            kind=PairKind.EFFECT_VS_NONE,
        )
        for i in range(n)
    ]
    return Candidates(pairs=pairs, blocks=[], headlines=[], out_of_domain=0)


def classification(pair_id: str) -> PairClassification:
    return PairClassification(
        pair_id=pair_id,
        verdict=Verdict.GENUINE,
        reason=Reason.POPULATION,
        also_differs=[],
        detail="synthetic detail",
        rationale="Synthetic rationale.",
    )


def test_the_stub_classifies_the_same_pairs_the_same_way_every_run() -> None:
    assert contradictions.stub(candidates(200)) == contradictions.stub(candidates(200))


def test_the_stub_is_marked_and_spread_over_every_verdict_and_reason() -> None:
    stubbed = contradictions.stub(candidates(400))
    verdicts = Counter(c.verdict for c in stubbed.classifications)

    assert stubbed.depth is None
    assert stubbed.candidates == 400
    # About one pair in five each way; the rest stay unclassified.
    assert 50 < verdicts[Verdict.GENUINE] < 110
    assert 50 < verdicts[Verdict.APPARENT] < 110
    assert {c.reason for c in stubbed.classifications} == set(Reason)
    assert all(c.detail == contradictions.STUB_DETAIL for c in stubbed.classifications)


def test_a_stubbed_apparent_pair_is_never_unclear() -> None:
    stubbed = contradictions.stub(candidates(400))

    assert not [
        c
        for c in stubbed.classifications
        if c.verdict is Verdict.APPARENT and c.reason is Reason.UNCLEAR
    ]


def test_the_file_round_trips(tmp_path: Path) -> None:
    stubbed = contradictions.stub(candidates(50))
    path = tmp_path / "classify" / contradictions.STUB_FILE

    contradictions.write(path, stubbed)

    assert contradictions.read(path) == stubbed


def test_a_pair_is_classified_once() -> None:
    with pytest.raises(ValidationError, match="classified once"):
        ClassifiedPairs(
            depth=Arm.SINGLE_SMALL,
            candidates=2,
            classifications=[classification("a"), classification("a")],
        )


def test_no_more_pairs_are_classified_than_there_are_candidates() -> None:
    with pytest.raises(ValidationError, match="more pairs classified"):
        ClassifiedPairs(
            depth=Arm.SINGLE_SMALL,
            candidates=1,
            classifications=[classification("a"), classification("b")],
        )


def test_the_stub_command_writes_beside_the_candidate_pairs(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "vitamin-d-depression"
    (run_dir / "compare").mkdir(parents=True)
    (run_dir / "compare" / "candidates.json").write_text(candidates(30).model_dump_json())

    result = CliRunner().invoke(
        app, ["export", "stub", "vitamin-d-depression", "--runs-dir", str(tmp_path / "runs")]
    )

    assert result.exit_code == 0, result.output
    stubbed = contradictions.read(run_dir / "classify" / contradictions.STUB_FILE)
    assert f"{len(stubbed.classifications)} of 30 pairs stubbed" in result.output


def test_the_stub_command_needs_the_candidate_pairs(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["export", "stub", "vitamin-d-depression", "--runs-dir", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "run --stage compare first" in result.output
