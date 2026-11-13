"""The blind second labeling pass: which records it takes, its templates, and agreement."""

from pathlib import Path
from typing import Any

import pytest
from test_labels import EXAMPLES, ROOT, VITAMIN_D, gold_entry, label, synthetic_abstract, write_gold

from claimgraph.design_tree import answers_for
from claimgraph.labels import LabelDraft, label_template, load_abstract_label, render_label
from claimgraph.models import Design
from claimgraph.relabel import agreement, agreement_table, match_findings, pick, write_templates

PMIDS = [str(999000001 + n) for n in range(27)]


def test_the_pick_is_fixed_by_the_seed() -> None:
    first = pick(PMIDS, seed=7)

    assert first == pick(PMIDS, seed=7)
    assert first == pick(list(reversed(PMIDS)), seed=7)
    assert first != pick(PMIDS, seed=8)
    assert len(first) == 5
    assert set(first) <= set(PMIDS)
    assert first == sorted(first, key=int)


def test_the_pick_needs_enough_records() -> None:
    with pytest.raises(ValueError, match="only 3 labeled"):
        pick(PMIDS[:3])


def labeled_gold(tmp_path: Path) -> Path:
    out = tmp_path / "vitamin-d-depression"
    write_gold(out, gold_entry())
    (out / "labels").mkdir()
    (out / "labels" / "999000001.toml").write_text((EXAMPLES / "999000001.toml").read_text())
    return out


def test_templates_are_blank_and_written_once(tmp_path: Path) -> None:
    out = labeled_gold(tmp_path)

    written = write_templates(out, VITAMIN_D, count=1)

    assert written == ["999000001"]
    # Blank, so the first pass is not shown.
    assert (out / "relabel" / "999000001.toml").read_text() == label_template(
        gold_entry(), VITAMIN_D
    )
    with pytest.raises(ValueError, match="already"):
        write_templates(out, VITAMIN_D, count=1)


def test_the_second_pass_draws_only_records_with_findings(tmp_path: Path) -> None:
    out = labeled_gold(tmp_path)
    off_topic = gold_entry(abstract=synthetic_abstract(pmid="999000002"))
    write_gold(out, gold_entry(), off_topic)
    no_findings = LabelDraft(
        pmid="999000002",
        design="rct",
        sample_size=40,
        note="Synthetic: nothing in the outcome domain.",
        design_answers=answers_for(Design.RCT),
    )
    (out / "labels" / "999000002.toml").write_text(render_label(off_topic, VITAMIN_D, no_findings))

    with pytest.raises(ValueError, match="only 1 labeled records with findings"):
        write_templates(out, VITAMIN_D, count=2)
    assert write_templates(out, VITAMIN_D, count=1) == ["999000001"]


def test_templates_wait_until_every_gold_record_is_labeled(tmp_path: Path) -> None:
    out = labeled_gold(tmp_path)
    write_gold(out, gold_entry(), gold_entry(abstract=synthetic_abstract(pmid="999000002")))

    with pytest.raises(ValueError, match="999000002"):
        write_templates(out, VITAMIN_D, count=1)
    assert not (out / "relabel").exists()


def finding(**overrides: Any) -> dict[str, Any]:
    return {
        "quote": "Synthetic depression scores fell more with synthetic vitamin D than with "
        "placebo (p = 0.01).",
        "outcome_reported": "synthetic depression score",
        "effect": "decreased",
        "is_primary": True,
        "is_subgroup": False,
        "dimensions": {"baseline_vitamin_d_status": "below_20_ng_ml"},
    } | overrides


REMISSION = {
    "quote": "Synthetic remission rates did not differ between groups.",
    "outcome_reported": "synthetic remission",
    "effect": "no_change",
    "is_primary": False,
}


def test_findings_match_on_quote_and_subgroup_preferring_the_same_outcome() -> None:
    first = [finding(), finding(**REMISSION)]
    # The same sentence twice: the one with the same outcome is the match.
    second = [finding(outcome_reported="synthetic mood"), finding(), finding(**REMISSION)]

    pairs = match_findings(label(finding=first).findings, label(finding=second).findings)

    assert [(a.outcome_reported, b.outcome_reported) for a, b in pairs] == [
        ("synthetic depression score", "synthetic depression score"),
        ("synthetic remission", "synthetic remission"),
    ]


def test_agreement_counts_each_field() -> None:
    first = label(sample_size=120, finding=[finding(), finding(**REMISSION)])
    second = label(
        sample_size=118,
        finding=[
            finding(outcome_reported="Synthetic depression score "),
            finding(
                **REMISSION | {"outcome_reported": "synthetic response", "effect": "decreased"}
            ),
            finding(
                quote="Synthetic remission rates did not differ between groups.",
                is_subgroup=True,
                is_primary=False,
                subgroup="synthetic women",
            ),
        ],
    )

    result = agreement({"999000001": first}, {"999000001": second})

    assert (result.records, result.first_findings, result.second_findings) == (1, 2, 3)
    assert result.matched_findings == 2
    fields = {f.name: (f.agree, f.compared) for f in result.fields}
    assert fields["design"] == (1, 1)
    assert fields["sample_size"] == (0, 1)
    # Case and spacing aside, the first outcome agrees and the second does not.
    assert fields["outcome_reported"] == (1, 2)
    assert fields["effect"] == (1, 2)
    assert fields["is_primary"] == (2, 2)
    assert fields["baseline_vitamin_d_status"] == (2, 2)
    table = agreement_table(result)
    assert "| sample_size | 0 of 1 |" in table
    assert "2 in the first pass, 3 in the second, 2 matched" in table


def test_agreement_needs_a_first_pass_for_each_record() -> None:
    with pytest.raises(ValueError, match="999000002"):
        agreement({}, {"999000002": label(pmid="999000002")})


def test_relabel_then_agreement_from_the_command_line(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from claimgraph import cli

    out = labeled_gold(tmp_path)
    write_templates(out, VITAMIN_D, count=1)
    config = str(ROOT / "questions" / "vitamin-d-depression.toml")
    runner = CliRunner()

    unfinished = runner.invoke(cli.app, ["gold", "agreement", config, "--gold-dir", str(tmp_path)])
    (out / "relabel" / "999000001.toml").write_text((EXAMPLES / "999000001.toml").read_text())
    done = runner.invoke(cli.app, ["gold", "agreement", config, "--gold-dir", str(tmp_path)])

    assert unfinished.exit_code == 1
    assert "999000001.toml" in unfinished.output
    assert done.exit_code == 0, done.output
    assert "| design | 1 of 1 |" in done.output
    assert load_abstract_label(out / "relabel" / "999000001.toml").pmid == "999000001"
