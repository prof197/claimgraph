"""The extraction eval: model output scored field by field against the hand labels.

Study fields are scored per abstract. Findings are matched to the labels on the quote, the same
rule the second labeling pass uses, and each finding field is then scored as precision,
correct over the model's findings, and recall, correct over the labeled findings. Outcomes are
correct when they name the same measure. Everything is read from the committed recordings.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from claimgraph.batch import extract_batched
from claimgraph.config import QuestionConfig
from claimgraph.design_tree import diverges, settle
from claimgraph.extract import (
    DesignSource,
    ExtractedStudy,
    ExtractionResult,
    ModelChoice,
    gold_replay_dir,
)
from claimgraph.gold import GoldAbstract
from claimgraph.labels import AbstractLabel, load_abstract_label, read_gold
from claimgraph.measures import abstract_text, same_measure
from claimgraph.models import Design, Record
from claimgraph.relabel import Agreement, agreement, match_findings

# The README block that eval extraction --readme writes.
EXTRACTION_BLOCK = "extraction-eval"
RERUN_BLOCK = "extraction-rerun"


class Count(Record):
    agree: int
    of: int


class FieldScore(Record):
    name: str
    correct: int
    # Findings the model gave and findings the labels hold: precision and recall denominators.
    model: int
    gold: int


class WrongDesign(Record):
    pmid: str
    gold: Design
    model: Design | None
    # "publication type", "question KEY" (the first answer off the gold design's path), or
    # "invalid output".
    cause: str


class ExtractionScores(Record):
    model: str
    abstracts: int
    invalid: int
    design: Count
    design_answers_alone: Count
    sample_size: Count
    findings: list[FieldScore]
    quotes_checked: int
    quote_misses: int
    quotes_dropped: int
    off_topic_empty: Count
    design_other: int
    wrong_designs: list[WrongDesign]


def _size(value: int | str | None) -> int | None:
    return None if value in (None, "not_reported") else int(value)


def _cause(study: ExtractedStudy, design: Design) -> str:
    alone = diverges(study.design_answers, design)
    if study.design_from is not DesignSource.PUBLICATION_TYPES:
        return f"question {alone}"
    return "publication type" + ("" if alone is None else f"; its answers leave at {alone}")


def score_extraction(
    gold: Sequence[GoldAbstract],
    labels: Mapping[str, AbstractLabel],
    results: Sequence[ExtractionResult],
    config: QuestionConfig,
) -> ExtractionScores:
    by_pmid = {r.pmid: r for r in results}
    missing = [g.abstract.pmid for g in gold if g.abstract.pmid not in by_pmid]
    if missing:
        raise ValueError(f"no extraction for {', '.join(missing)}")
    names = ["finding", "outcome_reported", "effect", "is_primary", "is_subgroup"] + [
        d.name for d in config.dimensions
    ]
    correct: Counter[str] = Counter()
    design = alone = size = off_topic = off_topic_empty = invalid = other = 0
    model_findings = gold_findings = 0
    wrong: list[WrongDesign] = []
    for entry in gold:
        pmid = entry.abstract.pmid
        lab, result = labels[pmid], by_pmid[pmid]
        study = result.study
        gold_findings += len(lab.findings)
        off_topic += not entry.on_topic
        if study is None:
            invalid += 1
            wrong.append(
                WrongDesign(pmid=pmid, gold=lab.design, model=None, cause="invalid output")
            )
            continue
        off_topic_empty += not entry.on_topic and not study.findings
        other += study.design is Design.OTHER
        design += study.design is lab.design
        alone += settle(study.design_answers) is lab.design
        if study.design is not lab.design:
            wrong.append(
                WrongDesign(
                    pmid=pmid, gold=lab.design, model=study.design, cause=_cause(study, lab.design)
                )
            )
        size += _size(study.sample_size) == _size(lab.sample_size)
        model_findings += len(study.findings)
        text = abstract_text(entry.abstract)
        for found, labeled in match_findings(study.findings, lab.findings):
            correct["finding"] += 1
            correct["outcome_reported"] += same_measure(
                found.outcome_reported, labeled.outcome_reported, text
            )
            correct["effect"] += found.effect is labeled.effect
            correct["is_primary"] += found.is_primary == labeled.is_primary
            correct["is_subgroup"] += found.is_subgroup == labeled.is_subgroup
            for d in config.dimensions:
                correct[d.name] += found.dimensions.get(d.name) == labeled.dimensions.get(d.name)
    n = len(gold)
    return ExtractionScores(
        model=results[0].model if results else "",
        abstracts=n,
        invalid=invalid,
        design=Count(agree=design, of=n),
        design_answers_alone=Count(agree=alone, of=n),
        sample_size=Count(agree=size, of=n),
        findings=[
            FieldScore(name=name, correct=correct[name], model=model_findings, gold=gold_findings)
            for name in names
        ],
        quotes_checked=sum(r.quotes_checked for r in results),
        quote_misses=sum(len(r.quote_misses) for r in results),
        quotes_dropped=sum(r.quotes_dropped for r in results),
        off_topic_empty=Count(agree=off_topic_empty, of=off_topic),
        design_other=other,
        wrong_designs=wrong,
    )


def _rate(agree: int, of: int) -> str:
    return f"{agree} of {of} ({100 * agree / of:.0f}%)" if of else f"{agree} of 0"


def extraction_table(scores: ExtractionScores) -> str:
    finding_rows = [
        (
            "finding, matched on quote" if f.name == "finding" else f.name,
            _rate(f.correct, f.model),
            _rate(f.correct, f.gold),
        )
        for f in scores.findings
    ]
    lines = [
        f"Extraction by {scores.model} on {scores.abstracts} gold abstracts, scored from the "
        f"committed recordings; {scores.invalid} invalid after the retry.",
        "",
        "| Study field | Correct |",
        "|---|---|",
        f"| design | {_rate(scores.design.agree, scores.design.of)} |",
        "| design, from the model's answers alone | "
        f"{_rate(scores.design_answers_alone.agree, scores.design_answers_alone.of)} |",
        f"| sample size | {_rate(scores.sample_size.agree, scores.sample_size.of)} |",
        "",
        "| Finding field | Precision | Recall |",
        "|---|---|---|",
        *(f"| {name} | {p} | {r} |" for name, p, r in finding_rows),
        "",
        f"Quotes verbatim on the first output: "
        f"{_rate(scores.quotes_checked - scores.quote_misses, scores.quotes_checked)}; "
        f"{scores.quotes_dropped} dropped. Off-topic abstracts with no findings: "
        f"{_rate(scores.off_topic_empty.agree, scores.off_topic_empty.of)}. Design other: "
        f"{scores.design_other} of {scores.abstracts}.",
    ]
    return "\n".join(lines) + "\n"


def wrong_design_lines(scores: ExtractionScores) -> list[str]:
    return [
        f"{w.pmid}: labeled {w.gold}, extracted {w.model or 'nothing'}, from {w.cause}"
        for w in scores.wrong_designs
    ]


# From the committed gold set and recordings


def _gold(
    gold_root: Path, config: QuestionConfig
) -> tuple[list[GoldAbstract], dict[str, AbstractLabel]]:
    out_dir = gold_root / config.id
    gold = read_gold(out_dir)
    labels = {
        g.abstract.pmid: load_abstract_label(out_dir / "labels" / f"{g.abstract.pmid}.toml")
        for g in gold
    }
    return gold, labels


def _replayed(
    gold: Sequence[GoldAbstract], replay_dir: Path, config: QuestionConfig, choice: ModelChoice
) -> list[ExtractionResult]:
    run = extract_batched([g.abstract for g in gold], config, replay_dir, choice, None)
    if run.missing:
        raise ValueError(
            f"no recording in {replay_dir} for {', '.join(run.missing)}; re-record with "
            "gold record --live after a dry run"
        )
    return run.results


def gold_scores(
    gold_root: Path,
    replay_root: Path,
    config: QuestionConfig,
    choice: ModelChoice = ModelChoice.HAIKU,
) -> ExtractionScores:
    gold, labels = _gold(gold_root, config)
    results = _replayed(gold, gold_replay_dir(replay_root, config, choice), config, choice)
    return score_extraction(gold, labels, results, config)


def rerun_agreement(
    gold_root: Path,
    replay_root: Path,
    second_root: Path,
    config: QuestionConfig,
    choice: ModelChoice = ModelChoice.HAIKU,
) -> Agreement:
    """How far two live runs of the same requests agree, since sampling cannot be pinned."""
    gold, _ = _gold(gold_root, config)
    first = _replayed(gold, gold_replay_dir(replay_root, config, choice), config, choice)
    second = _replayed(gold, gold_replay_dir(second_root, config, choice), config, choice)

    def studies(results: Sequence[ExtractionResult]) -> dict[str, ExtractedStudy]:
        return {r.pmid: r.study for r in results if r.study is not None}

    a, b = studies(first), studies(second)
    both = sorted(a.keys() & b.keys())
    texts = {g.abstract.pmid: abstract_text(g.abstract) for g in gold}
    return agreement({p: a[p] for p in both}, {p: b[p] for p in both}, texts)
