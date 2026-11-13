"""A blind second labeling pass over a few gold records, to measure how consistent the labels are.

The records are drawn at random, with a fixed seed, from the labeled gold records that have
findings, since a review or an off-topic record carries only its design and sample size. Their
templates go in the question's relabel/ directory, blank, and the review page labels them there
without showing the first pass. Agreement between the passes is counted field by field.
"""

import random
import re
from collections.abc import Mapping, Sequence
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
from claimgraph.measures import abstract_text, same_measure
from claimgraph.models import Record

SEED = 20261112
COUNT = 5
RELABEL_DIR = "relabel"
# Inside relabel/: the first-pass labels as they stood when the second pass was drawn, so a
# later correction to a gold label does not move the agreement.
FIRST_PASS_DIR = "first"
# The README block that gold agreement --readme writes.
AGREEMENT_BLOCK = "gold-agreement"


def pick(labeled: Sequence[str], seed: int = SEED, count: int = COUNT) -> list[str]:
    """The records for the second pass: a seeded random draw, whatever order they come in."""
    pool = sorted(labeled, key=int)
    if len(pool) < count:
        raise ValueError(
            f"only {len(pool)} labeled records with findings; the second pass takes {count}"
        )
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
    with_findings = [
        p for p in report.labeled if load_abstract_label(out_dir / "labels" / f"{p}.toml").findings
    ]
    picks = pick(with_findings, count=count)
    target.mkdir(parents=True, exist_ok=True)
    for pmid in picks:
        (target / f"{pmid}.toml").write_text(label_template(gold[pmid], config))
    kept = target / FIRST_PASS_DIR
    kept.mkdir()
    for pmid in picks:
        (kept / f"{pmid}.toml").write_bytes((out_dir / "labels" / f"{pmid}.toml").read_bytes())
    return picks


def read_passes(
    out_dir: Path, config: QuestionConfig
) -> tuple[dict[str, AbstractLabel], dict[str, AbstractLabel]]:
    """Both passes over the relabeled records: the first as kept when the second was drawn.

    Raises ValueError naming any unfinished file or any record without a kept first pass.
    """
    gold = {g.abstract.pmid: g for g in read_gold(out_dir)}
    paths = sorted((out_dir / RELABEL_DIR).glob("*.toml"))
    if not paths:
        raise ValueError("no second pass yet; run gold relabel first")
    problems = [p.name for p in paths if check_label_file(p, gold, config)]
    if problems:
        raise ValueError(f"finish the second pass first: {', '.join(problems)}")
    second = {p.stem: load_abstract_label(p) for p in paths}
    kept = out_dir / RELABEL_DIR / FIRST_PASS_DIR
    missing = [p for p in second if not (kept / f"{p}.toml").exists()]
    if missing:
        raise ValueError(f"no first pass kept from the draw for {', '.join(missing)}")
    first = {pmid: load_abstract_label(kept / f"{pmid}.toml") for pmid in second}
    return first, second


def abstract_texts(out_dir: Path) -> dict[str, str]:
    return {g.abstract.pmid: abstract_text(g.abstract) for g in read_gold(out_dir)}


# Agreement


def _text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _same_sentence(a: FindingLabel, b: FindingLabel) -> bool:
    same_subgroup = (a.subgroup and _text(a.subgroup)) == (b.subgroup and _text(b.subgroup))
    return normalize_quote(a.quote) == normalize_quote(b.quote) and same_subgroup


def _words(outcome: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", outcome.casefold()))


def match_findings(
    first: Sequence[FindingLabel], second: Sequence[FindingLabel]
) -> list[tuple[FindingLabel, FindingLabel]]:
    """Pairs of findings the two passes share, in the first pass's order.

    Findings pair on quote and subgroup, never on the outcome's wording, so the outcome is
    scored as a field rather than deciding the match. When one sentence holds several findings,
    they pair by the most outcome words in common, then in the order they were labeled.
    """
    candidates = sorted(
        (-len(_words(a.outcome_reported) & _words(b.outcome_reported)), i, j)
        for i, a in enumerate(first)
        for j, b in enumerate(second)
        if _same_sentence(a, b)
    )
    pairs: list[tuple[int, int]] = []
    for _, i, j in candidates:
        if all(i != p and j != q for p, q in pairs):
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


def agreement(
    first: Mapping[str, AbstractLabel],
    second: Mapping[str, AbstractLabel],
    abstracts: Mapping[str, str] | None = None,
) -> Agreement:
    """Field-level agreement between two labeling passes over the same records.

    Outcomes agree when they name the same measure, reading the abbreviations each record's
    abstract defines, so wording alone is not counted as a disagreement.
    """
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
            text = (abstracts or {}).get(pmid, "")
            count("outcome_reported", same_measure(fa.outcome_reported, fb.outcome_reported, text))
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


def replace_block(text: str, name: str, content: str) -> str:
    """The text with the generated block between its start and end markers replaced."""
    start, end = f"<!-- {name}:start -->\n", f"<!-- {name}:end -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise ValueError(f"expected one pair of {name} markers")
    head, rest = text.split(start)
    return head + start + content + end + rest.split(end, 1)[1]
