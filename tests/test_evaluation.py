"""The extraction eval: model output scored field by field against the gold labels."""

import json
from typing import Any

import pytest
from test_extract import finding, output, reply
from test_labels import ROOT, VITAMIN_D, gold_entry, label, synthetic_abstract

from claimgraph.design_tree import full_answers
from claimgraph.evaluation import (
    EXTRACTION_BLOCK,
    ExtractionScores,
    extraction_table,
    gold_scores,
    score_extraction,
)
from claimgraph.extract import ExtractionResult, read_extraction
from claimgraph.gold import GoldAbstract, Stratum
from claimgraph.labels import AbstractLabel
from claimgraph.models import Design
from claimgraph.relabel import replace_block
from claimgraph.retrieve import Abstract

SCORES = (
    "Synthetic depression scores fell more with synthetic vitamin D than with placebo (p = 0.01)."
)


def result(abstract: Abstract | None = None, valid: bool = True, **fields: Any) -> ExtractionResult:
    abstract = abstract or synthetic_abstract()
    study = (
        read_extraction(reply(json.dumps(output(**fields))), abstract, VITAMIN_D) if valid else None
    )
    findings = 0 if study is None else len(study.findings)
    return ExtractionResult(
        pmid=abstract.pmid,
        model="claude-haiku-5-5",
        study=study,
        errors=[] if valid else ["synthetic error", "synthetic error"],
        quotes_checked=findings,
        quote_misses=[],
        quotes_dropped=0,
        usage=[],
    )


def score(
    results: list[ExtractionResult],
    labels: list[AbstractLabel] | None = None,
    gold: list[GoldAbstract] | None = None,
) -> ExtractionScores:
    gold = gold or [gold_entry()]
    labels = labels or [label()]
    return score_extraction(gold, {lab.pmid: lab for lab in labels}, results, VITAMIN_D)


def fields(scores: ExtractionScores) -> dict[str, tuple[int, int, int]]:
    return {f.name: (f.correct, f.model, f.gold) for f in scores.findings}


def test_output_that_matches_its_label_scores_full_marks() -> None:
    scores = score([result()])

    assert (scores.design.agree, scores.design.of) == (1, 1)
    assert (scores.sample_size.agree, scores.sample_size.of) == (1, 1)
    assert set(fields(scores).values()) == {(1, 1, 1)}
    assert list(fields(scores)) == [
        "finding",
        "outcome_reported",
        "effect",
        "is_primary",
        "is_subgroup",
        "baseline_vitamin_d_status",
    ]


def test_an_extra_finding_costs_precision_and_a_missed_one_recall() -> None:
    extra = result(findings=[finding(), finding(quote=SCORES, outcome_reported="synthetic score")])
    two_labeled = label(
        finding=[
            {
                "quote": "Synthetic remission rates did not differ between groups.",
                "outcome_reported": "synthetic remission",
                "effect": "no_change",
                "is_primary": False,
                "is_subgroup": False,
                "dimensions": {"baseline_vitamin_d_status": "not_reported"},
            },
            {
                "quote": SCORES,
                "outcome_reported": "synthetic depression score",
                "effect": "decreased",
                "is_primary": True,
                "is_subgroup": False,
                "dimensions": {"baseline_vitamin_d_status": "not_reported"},
            },
        ]
    )

    assert fields(score([extra]))["finding"] == (1, 2, 1)
    assert fields(score([result()], [two_labeled]))["finding"] == (1, 1, 2)


def test_a_matched_finding_can_still_get_a_field_wrong() -> None:
    wrong = result(
        findings=[finding(outcome_direction="decreased", baseline_vitamin_d_status="mixed")]
    )

    scored = fields(score([wrong]))

    assert scored["finding"] == (1, 1, 1)
    assert scored["effect"] == (0, 1, 1)
    assert scored["baseline_vitamin_d_status"] == (0, 1, 1)
    assert scored["is_primary"] == (1, 1, 1)


def test_outcomes_score_as_the_same_measure_not_the_same_words() -> None:
    reworded = result(findings=[finding(outcome_reported="remission (synthetic)")])
    other = result(findings=[finding(outcome_reported="synthetic relapse")])

    assert fields(score([reworded]))["outcome_reported"] == (1, 1, 1)
    assert fields(score([other]))["outcome_reported"] == (0, 1, 1)


def test_sample_size_not_reported_matches_null() -> None:
    assert score([result(sample_size=None)], [label(sample_size="not_reported")]).sample_size.agree


def tagged(*types: str) -> Abstract:
    return synthetic_abstract().model_copy(update={"publication_types": list(types)})


def test_design_is_scored_with_and_without_publication_types_first() -> None:
    # Tagged a trial, labeled a cohort, and answered as a cohort: the tag makes it wrong.
    secondary = tagged("Randomized Controlled Trial")
    cohort = result(secondary, design_answers=full_answers(Design.COHORT))

    scores = score([cohort], [label(design="cohort")], [gold_entry(abstract=secondary)])

    assert (scores.design.agree, scores.design_answers_alone.agree) == (0, 1)
    (wrong,) = scores.wrong_designs
    assert (wrong.pmid, wrong.gold, wrong.model) == ("999000001", Design.COHORT, Design.RCT)
    assert wrong.cause == "publication type"


def test_a_wrong_design_from_the_answers_names_the_question() -> None:
    nonrandom = result(design_answers=full_answers(Design.RCT) | {"by_chance": "no"})

    (wrong,) = score([nonrandom]).wrong_designs

    assert wrong.model is Design.NON_RANDOMIZED_TRIAL
    assert wrong.cause == "question by_chance"


def test_output_invalid_after_the_retry_scores_nothing() -> None:
    scores = score([result(valid=False)])

    assert scores.invalid == 1
    assert scores.design.agree == 0
    assert fields(scores)["finding"] == (0, 0, 1)
    assert scores.wrong_designs[0].cause == "invalid output"


def test_off_topic_abstracts_should_get_no_findings() -> None:
    off = gold_entry(Stratum.OFF_TOPIC).model_copy(update={"on_topic": False})
    empty = label(finding=[], note="Synthetic: off topic.")

    assert score([result(findings=[])], [empty], [off]).off_topic_empty.agree == 1
    assert score([result()], [empty], [off]).off_topic_empty.agree == 0


def test_design_other_is_counted() -> None:
    mendelian = result(
        design_answers=full_answers(Design.COHORT) | {"proxy_or_event": "yes"}, findings=[]
    )

    assert (
        score([mendelian], [label(design="other", finding=[], note="Synthetic.")]).design_other == 1
    )


def test_the_table_gives_counts_and_rates() -> None:
    table = extraction_table(score([result(findings=[finding(outcome_direction="decreased")])]))

    assert "| design, publication types first | 1 of 1 (100%) |" in table
    assert "| design, review tags first and the answers otherwise | 1 of 1 (100%) |" in table
    assert "| effect | 0 of 1 (0%) | 0 of 1 (0%) |" in table
    assert "| finding, matched on quote | 1 of 1 (100%) | 1 of 1 (100%) |" in table


def test_every_label_needs_a_result() -> None:
    with pytest.raises(ValueError, match="999000001"):
        score([])


def test_review_tags_first_keeps_review_tags_and_ignores_a_trial_tag() -> None:
    # A secondary analysis tagged a trial, answered as the cohort it is labeled.
    secondary = tagged("Randomized Controlled Trial")
    cohort = result(secondary, design_answers=full_answers(Design.COHORT))
    # A review tagged systematic, whose answers say narrative review.
    review_tag = tagged("Systematic Review")
    narrative = result(review_tag, design_answers=full_answers(Design.NARRATIVE_REVIEW))

    scores = score(
        [cohort],
        [label(design="cohort")],
        [gold_entry(abstract=secondary)],
    )
    review = score(
        [narrative],
        [label(design="systematic_review", finding=[], review_summary="Synthetic summary.")],
        [gold_entry(abstract=review_tag)],
    )

    assert (scores.design.agree, scores.design_review_tags_first.agree) == (0, 1)
    assert (review.design_answers_alone.agree, review.design_review_tags_first.agree) == (0, 1)


def test_the_readme_shows_the_current_extraction_scores() -> None:
    # Generated by eval extraction --readme from the committed recordings, never typed by hand.
    readme = (ROOT / "README.md").read_text()
    table = extraction_table(gold_scores(ROOT / "evals" / "gold", ROOT / "replay", VITAMIN_D))

    assert replace_block(readme, EXTRACTION_BLOCK, table) == readme
