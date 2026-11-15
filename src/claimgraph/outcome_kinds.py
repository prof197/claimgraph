"""Outcome kinds for the normalization eval: each reported string labeled by what it measures.

Normalization groups strings by what they measure, so a pair's verdict follows from the kinds
of its two strings. One label per string therefore decides every pair, not only a drawn sample:
out of domain if either string is, merge if both share a kind, separate otherwise.
"""

import re
import tomllib
from collections.abc import Collection, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, ValidationError

from claimgraph import outcome_pairs
from claimgraph.labels import OutcomePairLabel, PairVerdict
from claimgraph.models import NonBlank, Record
from claimgraph.retrieve import write_atomic

LABEL_FILE = "outcome_kinds.toml"


class Kind(StrEnum):
    SCORE = "score"
    CASE_STATUS = "case_status"
    REMISSION = "remission"
    RESPONSE = "response"
    OUT_OF_DOMAIN = "out_of_domain"


class KindLabel(Record):
    string: NonBlank
    # Blank until the string is labeled.
    kind: Kind | Literal[""] = ""
    unsure: bool = False


def verdict(a: Kind, b: Kind) -> PairVerdict:
    """The pair verdict two strings' kinds give."""
    if Kind.OUT_OF_DOMAIN in (a, b):
        return PairVerdict.OUT_OF_DOMAIN
    return PairVerdict.MERGE if a == b else PairVerdict.SEPARATE


# The label file


def render(question: str, labels: Sequence[KindLabel]) -> str:
    """The label file, sorted by string so a string is easy to find by hand."""
    kinds = ", ".join(f'"{k}"' for k in Kind)
    lines = [
        f"# Outcome kinds for {question}: {len(labels)} strings.",
        f"# Set each kind to one of {kinds},",
        "# by the rules in evals/gold/LABELING.md, here or on the page that gold outcome-kinds",
        "# review serves, and add unsure = true where a string is too vague to place. Label",
        f"# every string before opening the mapping or {outcome_pairs.SAMPLE_FILE}: both",
        "# show the model's grouping.",
    ]
    for label in sorted(labels, key=lambda label: (label.string.casefold(), label.string)):
        lines += [
            "",
            "[[outcome]]",
            f"string = {outcome_pairs.toml_string(label.string)}",
            f'kind = "{label.kind}"',
        ]
        if label.unsure:
            lines.append("unsure = true")
    return "\n".join(lines) + "\n"


def write(path: Path, question: str, labels: Sequence[KindLabel]) -> None:
    write_atomic(path, render(question, labels).encode())


def write_template(out_dir: Path, question: str, strings: Sequence[str]) -> Path:
    """Write the label file with every string and no kinds; never over one that exists."""
    path = out_dir / LABEL_FILE
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path, question, [KindLabel(string=s) for s in strings])
    return path


class _KindFile(Record):
    outcome: list[KindLabel] = Field(default_factory=list[KindLabel])


def load(path: Path) -> list[KindLabel]:
    """Every entry of the label file, or ValueError if any entry does not read."""
    with path.open("rb") as f:
        labels = _KindFile.model_validate(tomllib.load(f)).outcome
    seen: set[str] = set()
    for label in labels:
        if label.string in seen:
            raise ValueError(f'"{label.string}" is listed more than once')
        seen.add(label.string)
    return labels


def _messages(error: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in error.errors()]


class KindReport(Record):
    # The strings the pairs were drawn from, or None when there is no draw.
    drawn: int | None
    labels: list[KindLabel]
    problems: list[str]

    def count(self, kind: Kind) -> int:
        return sum(label.kind == kind for label in self.labels)


def check(out_dir: Path) -> KindReport:
    """Check the label file entry by entry, against the draw if there is one."""
    sample_path = out_dir / outcome_pairs.SAMPLE_FILE
    drawn = outcome_pairs.load_sample(sample_path) if sample_path.exists() else None
    problems: list[str] = []
    try:
        with (out_dir / LABEL_FILE).open("rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError) as error:
        data = {}
        problems.append(str(error))
    raw: Any = data.get("outcome", [])
    entries = cast(list[Any], raw) if isinstance(raw, list) else []
    if not isinstance(raw, list):
        problems.append("strings are written [[outcome]], one table each")
    if stray := sorted(set(data) - {"outcome"}):
        problems.append(f"unknown tables: {', '.join(stray)}")
    labels: list[KindLabel] = []
    seen: set[str] = set()
    for n, entry in enumerate(entries, start=1):
        try:
            label = KindLabel.model_validate(entry)
        except ValidationError as error:
            problems += [f"string {n}: {m}" for m in _messages(error)]
            continue
        if label.string in seen:
            problems.append(f'string {n}: "{label.string}" is listed more than once')
            continue
        seen.add(label.string)
        labels.append(label)
    if drawn is not None:
        if len(entries) != drawn.strings:
            problems.append(f"the draw has {drawn.strings} strings; the file lists {len(entries)}")
        in_pairs = {s for p in drawn.pairs for s in (p.a, p.b)}
        if missing := sorted(in_pairs - seen):
            more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
            listed = "; ".join(missing[:5]) + more
            problems.append(f"{len(missing)} strings in drawn pairs are not in the file: {listed}")
    if unlabeled := sum(label.kind == "" for label in labels):
        problems.append(f"{unlabeled} of {len(labels)} strings have no kind yet")
    return KindReport(
        drawn=None if drawn is None else drawn.strings, labels=labels, problems=problems
    )


# Corrections made after labeling

CORRECTIONS_FILE = "outcome_kind_corrections.toml"


class Found(StrEnum):
    """How a correction was found. One found only where a model disagreed with the labels can
    raise that model's score but never lower it, so the eval reports it before and after."""

    PAIR_VERDICTS = "pair_verdicts"  # comparing the kinds with the first-pass pair verdicts
    FIRST_PROPOSAL = "first_proposal"  # where the first proposal disagreed, after scoring
    RULES_PROPOSAL = "rules_proposal"  # where the proposal under the rules prompt disagreed

    @property
    def described(self) -> str:
        return {
            Found.PAIR_VERDICTS: "The first-pass pair verdicts, before scoring",
            Found.FIRST_PROPOSAL: "The first proposal's disagreements, after scoring",
            Found.RULES_PROPOSAL: "The rules proposal's disagreements, after scoring",
        }[self]


class Correction(Record):
    string: NonBlank
    was: Kind
    now: Kind
    found: Found
    rule: NonBlank


class _CorrectionFile(Record):
    correction: list[Correction] = Field(default_factory=list[Correction])


def load_corrections(path: Path) -> list[Correction]:
    with path.open("rb") as f:
        return _CorrectionFile.model_validate(tomllib.load(f)).correction


def before(
    labels: Sequence[KindLabel], corrections: Sequence[Correction], found: Collection[Found]
) -> list[KindLabel]:
    """The labels as they were before the corrections found these ways."""
    undo = {c.string: c for c in corrections if c.found in found}
    out: list[KindLabel] = []
    for label in labels:
        c = undo.pop(label.string, None)
        if c is not None:
            if label.kind != c.now:
                raise ValueError(f"{c.string!r} is labeled {label.kind!r}, not {c.now.value!r}")
            label = label.model_copy(update={"kind": c.was})
        out.append(label)
    if undo:
        raise ValueError(f"corrected strings not in the kind file: {', '.join(sorted(undo))}")
    return out


def correction_rows(corrections: Sequence[Correction]) -> list[str]:
    """The corrections as rows of the gold README's table."""
    return [
        f"| {c.string} | `{c.was}` | `{c.now}` | {c.rule} | {c.found.described} |"
        for c in corrections
    ]


# Pair verdicts entered by hand, compared with the ones the kinds give

_PAIR_TABLE = re.compile(r"^\[\[pair\]\][ \t]*$", re.MULTILINE)
# A comment after the verdict, such as "# unsure", which TOML itself drops.
_VERDICT_COMMENT = re.compile(r"^verdict\s*=[^#\n]*#\s*(.*\S)", re.MULTILINE)


class EnteredPair(Record):
    label: OutcomePairLabel
    comment: str | None


class EnteredPairs(Record):
    pairs: list[EnteredPair]
    blank: int
    problems: list[str]


def read_entered_pairs(path: Path) -> EnteredPairs:
    """The pairs given a verdict, read one table at a time so one typo costs only its pair."""
    text = path.read_text()
    starts = [m.start() for m in _PAIR_TABLE.finditer(text)]
    pairs: list[EnteredPair] = []
    blank = 0
    problems: list[str] = []
    for n, (start, end) in enumerate(zip(starts, [*starts[1:], len(text)], strict=True), 1):
        block = text[start:end]
        where = f"pair {n} (line {text.count(chr(10), 0, start) + 1})"
        try:
            raw: Any = tomllib.loads(block)["pair"][0]
        except tomllib.TOMLDecodeError as error:
            problems.append(f"{where}: not read: {error}")
            continue
        if cast(dict[str, Any], raw).get("verdict") == "":
            blank += 1
            continue
        try:
            label = OutcomePairLabel.model_validate(raw)
        except ValidationError as error:
            problems += [f"{where}: {m}" for m in _messages(error)]
            continue
        comment = match[1] if (match := _VERDICT_COMMENT.search(block)) else None
        pairs.append(EnteredPair(label=label, comment=label.note or comment))
    return EnteredPairs(pairs=pairs, blank=blank, problems=problems)


class Disagreement(Record):
    a: str
    b: str
    entered: PairVerdict
    derived: PairVerdict
    kind_a: Kind
    kind_b: Kind
    # Either string's kind is marked unsure.
    unsure: bool
    comment: str | None


class Comparison(Record):
    agree: int
    disagree: list[Disagreement]
    # Entered pairs with a string that has no kind yet.
    waiting: int


def compare(entered: Sequence[EnteredPair], labels: Sequence[KindLabel]) -> Comparison:
    kinds = {label.string: label for label in labels if isinstance(label.kind, Kind)}
    agree = waiting = 0
    disagree: list[Disagreement] = []
    for pair in entered:
        a, b = pair.label.key
        if a not in kinds or b not in kinds:
            waiting += 1
            continue
        kind_a, kind_b = cast(Kind, kinds[a].kind), cast(Kind, kinds[b].kind)
        derived = verdict(kind_a, kind_b)
        if derived == pair.label.verdict:
            agree += 1
            continue
        disagree.append(
            Disagreement(
                a=a,
                b=b,
                entered=pair.label.verdict,
                derived=derived,
                kind_a=kind_a,
                kind_b=kind_b,
                unsure=kinds[a].unsure or kinds[b].unsure,
                comment=pair.comment,
            )
        )
    disagree.sort(key=lambda d: (d.entered, d.derived, d.a.casefold()))
    return Comparison(agree=agree, disagree=disagree, waiting=waiting)
