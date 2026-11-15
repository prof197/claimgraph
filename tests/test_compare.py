"""Candidate contradiction pairs from the extracted findings.

Every study, outcome and trial ID here is synthetic.
"""

from pathlib import Path
from typing import Any

import pytest
from test_labels import ROOT, synthetic_abstract
from test_normalize import result

from claimgraph import compare, normalize
from claimgraph.compare import Direction
from claimgraph.extract import ExtractionResult
from claimgraph.models import Design, Effect, NormalizedOutcome, PairKind

SYMPTOMS = NormalizedOutcome(
    name="synthetic symptoms",
    members=["synthetic mood score", "synthetic low mood"],
    higher_is_better=False,
    reviewed=True,
)
REMISSION = NormalizedOutcome(
    name="synthetic remission",
    members=["synthetic remission"],
    higher_is_better=True,
    reviewed=True,
)
MAPPING = normalize.OutcomeMapping(
    outcomes=[SYMPTOMS, REMISSION], out_of_domain=["synthetic distress"]
)

Spec = tuple[str, Effect] | tuple[str, Effect, bool, bool]


def paper(pmid: str, *specs: Spec, design: Design = Design.RCT) -> ExtractionResult:
    """A paper with findings given as (outcome, effect[, primary, subgroup])."""
    base = result(*(s[0] for s in specs), pmid=pmid)
    assert base.study is not None
    findings = [
        f.model_copy(
            update={
                "effect": s[1],
                "is_primary": s[2] if len(s) > 2 else False,
                "is_subgroup": s[3] if len(s) > 3 else False,
                "finding_id": f"{pmid}-{i}",
            }
        )
        for i, (f, s) in enumerate(zip(base.study.findings, specs, strict=True), start=1)
    ]
    study = base.study.model_copy(update={"pmid": pmid, "findings": findings, "design": design})
    return base.model_copy(update={"study": study})


@pytest.mark.parametrize(
    ("effect", "higher_is_better", "expected"),
    [
        (Effect.DECREASED, False, Direction.BENEFIT),
        (Effect.INCREASED, False, Direction.HARM),
        (Effect.INCREASED, True, Direction.BENEFIT),
        (Effect.DECREASED, True, Direction.HARM),
        (Effect.NO_CHANGE, True, Direction.NONE),
    ],
)
def test_direction_reads_the_effect_by_the_outcome_s_polarity(
    effect: Effect, higher_is_better: bool, expected: Direction
) -> None:
    assert compare.direction(effect, higher_is_better) is expected


def test_a_pair_needs_directions_that_differ() -> None:
    assert compare.pair_kind(Direction.BENEFIT, Direction.NONE) is PairKind.EFFECT_VS_NONE
    assert compare.pair_kind(Direction.NONE, Direction.HARM) is PairKind.EFFECT_VS_NONE
    assert compare.pair_kind(Direction.HARM, Direction.BENEFIT) is PairKind.BENEFIT_VS_HARM
    assert compare.pair_kind(Direction.BENEFIT, Direction.BENEFIT) is None


def test_papers_sharing_a_trial_id_are_one_study_transitively() -> None:
    keys = compare.study_keys(
        {
            "999000001": ["SYNTHETIC:T1"],
            "999000002": ["SYNTHETIC:T2", "SYNTHETIC:T1"],
            "999000003": ["SYNTHETIC:T2"],
            "999000004": [],
        }
    )

    assert keys == {
        "999000001": "SYNTHETIC:T1",
        "999000002": "SYNTHETIC:T1",
        "999000003": "SYNTHETIC:T1",
        "999000004": "999000004",
    }


def test_the_headline_is_the_primary_finding_else_the_first_whole_sample_one() -> None:
    first, primary = paper(
        "999000001",
        ("synthetic mood score", Effect.NO_CHANGE),
        ("synthetic low mood", Effect.DECREASED, True, False),
    ).study.findings  # type: ignore[union-attr]
    group, whole = paper(
        "999000002",
        ("synthetic mood score", Effect.DECREASED, False, True),
        ("synthetic low mood", Effect.NO_CHANGE),
    ).study.findings  # type: ignore[union-attr]

    assert compare.headline([first, primary]) is primary
    assert compare.headline([group, whole]) is whole
    assert compare.headline([group]) is None


def trial_ids(*pmids: str) -> dict[str, list[str]]:
    return {p: [] for p in pmids}


def test_studies_pair_when_their_headlines_point_different_ways() -> None:
    found = compare.candidates(
        [
            paper("999000001", ("synthetic mood score", Effect.DECREASED)),
            paper("999000002", ("synthetic low mood", Effect.NO_CHANGE)),
            paper("999000003", ("synthetic mood score", Effect.INCREASED)),
            paper("999000004", ("synthetic low mood", Effect.DECREASED)),
        ],
        trial_ids("999000001", "999000002", "999000003", "999000004"),
        MAPPING,
    )

    assert {(p.study_a, p.study_b, p.kind) for p in found.pairs} == {
        ("999000001", "999000002", PairKind.EFFECT_VS_NONE),
        ("999000001", "999000003", PairKind.BENEFIT_VS_HARM),
        ("999000002", "999000003", PairKind.EFFECT_VS_NONE),
        ("999000002", "999000004", PairKind.EFFECT_VS_NONE),
        ("999000003", "999000004", PairKind.BENEFIT_VS_HARM),
    }
    assert [(b.outcome, b.studies, b.pairs) for b in found.blocks] == [("synthetic symptoms", 4, 5)]


def test_a_trial_never_pairs_with_itself_and_its_earliest_paper_leads() -> None:
    found = compare.candidates(
        [
            paper("999000002", ("synthetic mood score", Effect.NO_CHANGE)),
            paper("999000001", ("synthetic mood score", Effect.DECREASED)),
            paper("999000003", ("synthetic mood score", Effect.NO_CHANGE)),
        ],
        {"999000001": ["SYNTHETIC:T1"], "999000002": ["SYNTHETIC:T1"], "999000003": []},
        MAPPING,
    )

    lead = next(h for h in found.headlines if h.study == "SYNTHETIC:T1")
    assert (lead.pmid, lead.direction) == ("999000001", Direction.BENEFIT)
    assert [(p.study_a, p.study_b) for p in found.pairs] == [("999000003", "SYNTHETIC:T1")]


def test_a_study_with_only_subgroup_findings_is_counted_not_paired() -> None:
    found = compare.candidates(
        [
            paper("999000001", ("synthetic mood score", Effect.DECREASED, False, True)),
            paper("999000002", ("synthetic mood score", Effect.NO_CHANGE)),
        ],
        trial_ids("999000001", "999000002"),
        MAPPING,
    )

    assert found.pairs == []
    assert [(b.studies, b.subgroup_only) for b in found.blocks] == [(1, 1)]


def test_out_of_domain_and_review_findings_are_left_out() -> None:
    found = compare.candidates(
        [
            paper("999000001", ("synthetic distress", Effect.DECREASED)),
            paper("999000002", ("synthetic remission", Effect.INCREASED)),
            paper(
                "999000003",
                ("synthetic remission", Effect.NO_CHANGE),
                design=Design.SYSTEMATIC_REVIEW,
            ),
        ],
        trial_ids("999000001", "999000002", "999000003"),
        MAPPING,
    )

    assert found.out_of_domain == 1
    assert found.pairs == []
    assert [(b.outcome, b.studies) for b in found.blocks] == [("synthetic remission", 1)]


def test_every_outcome_string_must_be_in_the_mapping() -> None:
    with pytest.raises(ValueError, match="synthetic sleep"):
        compare.candidates(
            [paper("999000001", ("synthetic sleep", Effect.DECREASED))],
            trial_ids("999000001"),
            MAPPING,
        )


# The stage


def run_compare(tmp_path: Path) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    return CliRunner().invoke(
        cli.app,
        [
            "run",
            str(ROOT / "questions" / "vitamin-d-depression.toml"),
            "--stage",
            "compare",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--mappings-dir",
            str(tmp_path / "mappings"),
        ],
    )


def staged(tmp_path: Path, mapping: normalize.OutcomeMapping) -> Path:
    run_dir = tmp_path / "runs" / "vitamin-d-depression"
    (run_dir / "extract").mkdir(parents=True)
    papers = [
        paper("999000001", ("synthetic mood score", Effect.DECREASED)),
        paper("999000002", ("synthetic low mood", Effect.NO_CHANGE)),
    ]
    (run_dir / "extract" / "corpus-sonnet.jsonl").write_text(
        "".join(p.model_dump_json() + "\n" for p in papers)
    )
    abstracts = [
        synthetic_abstract().model_copy(update={"pmid": p.pmid, "trial_ids": []}) for p in papers
    ]
    (run_dir / "abstracts.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in abstracts))
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    path.write_text(normalize.mapping_toml(mapping.outcomes, ["Synthetic."], mapping.out_of_domain))
    return run_dir


def test_the_stage_prints_and_stores_the_block_sizes(tmp_path: Path) -> None:
    run_dir = staged(tmp_path, MAPPING)

    result = run_compare(tmp_path)

    assert result.exit_code == 0, result.output
    assert "1 candidate pairs from 2 studies" in result.output
    assert "     1 pairs   2 studies  synthetic symptoms" in result.output
    stored = compare.Candidates.model_validate_json(
        (run_dir / "compare" / "candidates.json").read_text()
    )
    assert [b.pairs for b in stored.blocks] == [1]


def test_the_stage_says_when_the_mapping_is_unreviewed(tmp_path: Path) -> None:
    unreviewed = MAPPING.model_copy(
        update={"outcomes": [o.model_copy(update={"reviewed": False}) for o in MAPPING.outcomes]}
    )
    staged(tmp_path, unreviewed)

    result = run_compare(tmp_path)

    assert result.exit_code == 0, result.output
    assert "unreviewed mapping" in result.output
