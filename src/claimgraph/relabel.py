"""A blind second labeling pass over a few gold records, to measure how consistent the labels are.

The records are drawn at random, with a fixed seed, from the labeled gold set. Their templates go
in the question's relabel/ directory, blank, and the review page labels them there without
showing the first pass. Agreement between the passes is counted field by field.
"""

import random
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from claimgraph.config import QuestionConfig
from claimgraph.labels import (
    AbstractLabel,
    FindingLabel,
    check_label_file,
    check_labels,
    label_template,
    load_abstract_label,
    normalize_quote,
    read_gold,
)
from claimgraph.models import Record

SEED = 20261112
COUNT = 5
RELABEL_DIR = "relabel"


def pick(labeled: Sequence[str], seed: int = SEED, count: int = COUNT) -> list[str]:
    """The records for the second pass: a seeded random draw, whatever order they come in."""
    pool = sorted(labeled, key=int)
    if len(pool) < count:
        raise ValueError(f"only {len(pool)} labeled records; the second pass takes {count}")
    return sorted(random.Random(seed).sample(pool, count), key=int)


def write_templates(out_dir: Path, config: QuestionConfig, count: int = COUNT) -> list[str]:
    """Blank templates for the second pass; drawn once, and only from a fully labeled gold set."""
    target = out_dir / RELABEL_DIR
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"the second pass is already drawn in {target}")
    report = check_labels(out_dir, config)
    unfinished = sorted(
        [*report.missing, *(name.removesuffix(".toml") for name in report.problems)]
    )
    if unfinished:
        raise ValueError(f"label every gold record first: {', '.join(unfinished)}")
    gold = {g.abstract.pmid: g for g in read_gold(out_dir)}
    picks = pick(report.labeled, count=count)
    target.mkdir(parents=True, exist_ok=True)
    for pmid in picks:
        (target / f"{pmid}.toml").write_text(label_template(gold[pmid], config))
    return picks


def read_passes(
    out_dir: Path, config: QuestionConfig
) -> tuple[dict[str, AbstractLabel], dict[str, AbstractLabel]]:
    """Both passes over the relabeled records; raises ValueError naming any unfinished file."""
    gold = {g.abstract.pmid: g for g in read_gold(out_dir)}
    paths = sorted((out_dir / RELABEL_DIR).glob("*.toml"))
    if not paths:
        raise ValueError("no second pass yet; run gold relabel first")
    problems = [p.name for p in paths if check_label_file(p, gold, config)]
    if problems:
        raise ValueError(f"finish the second pass first: {', '.join(problems)}")
    second = {p.stem: load_abstract_label(p) for p in paths}
    first = {
        pmid: load_abstract_label(out_dir / "labels" / f"{pmid}.toml")
        for pmid in second
        if (out_dir / "labels" / f"{pmid}.toml").exists()
    }
    return first, second


# Agreement


def _text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _same_sentence(a: FindingLabel, b: FindingLabel) -> bool:
    same_subgroup = (a.subgroup and _text(a.subgroup)) == (b.subgroup and _text(b.subgroup))
    return normalize_quote(a.quote) == normalize_quote(b.quote) and same_subgroup


def _same_finding(a: FindingLabel, b: FindingLabel) -> bool:
    return _same_sentence(a, b) and _text(a.outcome_reported) == _text(b.outcome_reported)


def match_findings(
    first: Sequence[FindingLabel], second: Sequence[FindingLabel]
) -> list[tuple[FindingLabel, FindingLabel]]:
    """Pairs of findings the two passes share, in the first pass's order.

    Findings pair on quote, outcome and subgroup, as the extraction eval pairs them, then on
    quote and subgroup alone, so a reworded outcome counts against the outcome field rather than
    as a missed finding.
    """
    pairs: list[tuple[int, int]] = []
    rule: Callable[[FindingLabel, FindingLabel], bool]
    for rule in (_same_finding, _same_sentence):
        for i, a in enumerate(first):
            if any(i == p for p, _ in pairs):
                continue
            taken = {q for _, q in pairs}
            j = next((j for j, b in enumerate(second) if j not in taken and rule(a, b)), None)
            if j is not None:
                pairs.append((i, j))
    return [(first[i], second[j]) for i, j in sorted(pairs)]


class FieldAgreement(Record):
    name: str
    agree: int
    compared: int


class Agreement(Record):
    records: int
    first_findings: int
    second_findings: int
    matched_findings: int
    # Study fields over records, then finding fields over matched findings.
    fields: list[FieldAgreement]


def agreement(first: Mapping[str, AbstractLabel], second: Mapping[str, AbstractLabel]) -> Agreement:
    """Field-level agreement between two labeling passes over the same records."""
    missing = sorted(p for p in second if p not in first)
    if missing:
        raise ValueError(f"no first-pass label for {', '.join(missing)}")
    counts: dict[str, list[int]] = {}

    def count(name: str, same: bool) -> None:
        agree, compared = counts.setdefault(name, [0, 0])
        counts[name] = [agree + same, compared + 1]

    matched = 0
    for pmid, b in second.items():
        a = first[pmid]
        count("design", a.design is b.design)
        count("sample_size", a.sample_size == b.sample_size)
        for fa, fb in match_findings(a.findings, b.findings):
            matched += 1
            count("outcome_reported", _text(fa.outcome_reported) == _text(fb.outcome_reported))
            count("effect", fa.effect is fb.effect)
            count("is_primary", fa.is_primary == fb.is_primary)
            count("is_subgroup", fa.is_subgroup == fb.is_subgroup)
            for name in sorted(fa.dimensions.keys() | fb.dimensions.keys()):
                count(name, fa.dimensions.get(name) == fb.dimensions.get(name))
    return Agreement(
        records=len(second),
        first_findings=sum(len(first[p].findings) for p in second),
        second_findings=sum(len(b.findings) for b in second.values()),
        matched_findings=matched,
        fields=[FieldAgreement(name=n, agree=a, compared=c) for n, (a, c) in counts.items()],
    )


def agreement_table(result: Agreement) -> str:
    lines = [
        f"Second labeling pass over {result.records} gold records. Findings: "
        f"{result.first_findings} in the first pass, {result.second_findings} in the second, "
        f"{result.matched_findings} matched on quote and subgroup.",
        "",
        "| Field | Agreement |",
        "|---|---|",
        *(f"| {f.name} | {f.agree} of {f.compared} |" for f in result.fields),
    ]
    return "\n".join(lines) + "\n"
