"""Outcome pairs for the normalization eval: drawn from the model's proposal, labeled by hand.

The eval scores the proposal before review, so it measures the model rather than the reviewer.
The strata therefore come from the recorded proposal, never from the mapping file the reviewer
edits. Pairs are drawn in disjoint strata, so the eval can report the size of each: the hard
cases, pairs the proposal grouped, and pairs it split that look related. Split pairs sharing no
acronym and no uncommon word are nearly all plain non-merges, so they are counted, not drawn.
"""

import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from itertools import combinations
from pathlib import Path

from pydantic import Field

from claimgraph.models import Record
from claimgraph.retrieve import write_atomic

SEED = 20261115
# About 200 pairs: every pair of named hard cases, then these.
PER_HARD_CASE = 10
QUOTAS_GROUPED = 75
QUOTAS_SPLIT_RELATED = 75
# A word in more than this share of the strings, such as "depression", relates nearly every
# pair, so it does not count as shared.
COMMON_SHARE = 0.1
SAMPLE_FILE = "outcome_pair_sample.json"


class Stratum(StrEnum):
    """Where a pair was drawn from. A pair falls in the first stratum it fits, in this order."""

    NAMED = "named"  # both strings are hard cases the question names
    CUT_OFF_VS_SCORE = "cut_off_vs_score"  # one scale with a cut-off, the same scale without
    CUT_OFF_VS_CASE = "cut_off_vs_case"  # a scale with a cut-off, a diagnosis or incidence
    INCIDENCE_VS_SEVERITY = "incidence_vs_severity"  # a diagnosis or incidence, a score
    GENERAL_VS_SCALE = "general_vs_scale"  # a general term, a named scale
    GROUPED = "grouped"  # the proposal put both under one outcome
    SPLIT_RELATED = "split_related"  # different outcomes, sharing an acronym or a word
    SPLIT_UNRELATED = "split_unrelated"  # counted, never drawn


HARD = (
    Stratum.NAMED,
    Stratum.CUT_OFF_VS_SCORE,
    Stratum.CUT_OFF_VS_CASE,
    Stratum.INCIDENCE_VS_SEVERITY,
    Stratum.GENERAL_VS_SCALE,
)
QUOTAS: dict[Stratum, int] = {
    Stratum.CUT_OFF_VS_SCORE: PER_HARD_CASE,
    Stratum.CUT_OFF_VS_CASE: PER_HARD_CASE,
    Stratum.INCIDENCE_VS_SEVERITY: PER_HARD_CASE,
    Stratum.GENERAL_VS_SCALE: PER_HARD_CASE,
    Stratum.GROUPED: QUOTAS_GROUPED,
    Stratum.SPLIT_RELATED: QUOTAS_SPLIT_RELATED,
}

_TOKEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
# A trailing version: PHQ-9, HAM-D17, BDI-II, BDI-Ia.
_VERSION = re.compile(r"(?:-?\d+|-(?:I{1,3}|IV|V)[a-z]?)+$")
# 25(OH)D is vitamin D, not an instrument; a bare Roman numeral is a version.
_NOT_ACRONYMS = frozenset({"OH", "II", "III", "IV"})
_WORD = re.compile(r"[a-z]{3,}")
_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "with",
        "for",
        "from",
        "that",
        "than",
        "this",
        "were",
        "was",
        "are",
        "between",
        "among",
        "after",
        "before",
        "during",
        "over",
        "under",
        "into",
        "versus",
        "per",
        "its",
        "their",
        "all",
        "any",
        "other",
        "not",
    ]
)
_CUT_OFF = re.compile(
    r"[≥≤]|>=|<=|[<>]\s*\d|\bcut[- ]?off|\bor (?:more|higher|above|greater)\b|\bthreshold"
    r"|\bclinically (?:relevant|significant)",
    re.IGNORECASE,
)
_CASE = re.compile(r"\b(?:incidence|incident|diagnos\w*|prevalen\w*|onset|cases?)\b", re.I)
_SEVERITY = re.compile(r"\b(?:severity|scores?|symptoms?)\b", re.IGNORECASE)
_GENERAL_WORDS = 3


def acronyms(text: str) -> set[str]:
    """Upper-case tokens such as instrument names, without version or hyphen: PHQ-9 is PHQ."""
    found: set[str] = set()
    for token in _TOKEN.findall(text):
        stem = _VERSION.sub("", token)
        if sum(c.isupper() for c in stem) >= 2 and stem.upper() not in _NOT_ACRONYMS:
            found.add(stem.replace("-", "").upper())
    return found


def has_cut_off(text: str) -> bool:
    return bool(_CUT_OFF.search(text))


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold())) - _STOPWORDS


def common_words(strings: Sequence[str]) -> list[str]:
    counts = Counter(w for s in strings for w in _words(s))
    return sorted(w for w, n in counts.items() if n > COMMON_SHARE * len(strings))


class Features(Record):
    acronyms: frozenset[str]
    words: frozenset[str]
    cut_off: bool
    case: bool
    severity: bool
    general: bool


def features(text: str, common: frozenset[str]) -> Features:
    found = acronyms(text)
    cut_off = has_cut_off(text)
    case = bool(_CASE.search(text))
    general = (
        not found
        and not cut_off
        and not case
        and not re.search(r"[\d()]", text)
        and len(text.split()) <= _GENERAL_WORDS
    )
    return Features(
        acronyms=frozenset(found),
        words=frozenset(_words(text) - common),
        cut_off=cut_off,
        case=case,
        severity=bool(_SEVERITY.search(text)) and not case,
        general=general,
    )


def stratum(a: Features, b: Features, grouped: bool, named: bool) -> Stratum:
    """The first stratum the pair fits. Grouped says whether the proposal merged the two."""
    if named:
        return Stratum.NAMED
    if a.cut_off != b.cut_off:
        cut, other = (a, b) if a.cut_off else (b, a)
        if cut.acronyms & other.acronyms:
            return Stratum.CUT_OFF_VS_SCORE
        if other.case:
            return Stratum.CUT_OFF_VS_CASE
    if not (a.cut_off or b.cut_off):
        if (a.case and b.severity) or (b.case and a.severity):
            return Stratum.INCIDENCE_VS_SEVERITY
        if (a.general and b.acronyms) or (b.general and a.acronyms):
            return Stratum.GENERAL_VS_SCALE
    if grouped:
        return Stratum.GROUPED
    if a.acronyms & b.acronyms or a.words & b.words:
        return Stratum.SPLIT_RELATED
    return Stratum.SPLIT_UNRELATED


class DrawnPair(Record):
    a: str
    b: str
    stratum: Stratum


class Draw(Record):
    pairs: list[DrawnPair]
    # Pairs available in each stratum, drawn or not; together they are every pair of strings.
    pools: dict[Stratum, int]
    common_words: list[str]


def draw(
    strings: Sequence[str],
    groups: Mapping[str, str],
    hard_cases: Sequence[str],
    seed: int = SEED,
    quotas: Mapping[Stratum, int] = QUOTAS,
) -> Draw:
    """Every pair of named hard cases, then each stratum's quota at random, or all if fewer."""
    common = common_words(strings)
    named = {h.casefold() for h in hard_cases}
    seen = {s: features(s, frozenset(common)) for s in strings}
    pools: dict[Stratum, list[tuple[str, str]]] = {s: [] for s in Stratum}
    for a, b in combinations(sorted(strings), 2):
        both_named = a.casefold() in named and b.casefold() in named
        pools[stratum(seen[a], seen[b], groups[a] == groups[b], both_named)].append((a, b))
    rng = random.Random(seed)
    pairs: list[DrawnPair] = []
    for s in Stratum:
        pool = pools[s]
        if s is Stratum.NAMED:
            taken = pool
        else:
            taken = sorted(rng.sample(pool, min(quotas.get(s, 0), len(pool))))
        pairs.extend(DrawnPair(a=a, b=b, stratum=s) for a, b in taken)
    return Draw(pairs=pairs, pools={s: len(p) for s, p in pools.items()}, common_words=common)


class PairSample(Record):
    """The drawn pairs and how they were drawn. Opened only after labeling, as it shows strata."""

    question: str
    seed: int
    model: str
    # The request key of the recorded proposal the strata come from.
    proposal: str
    strings: int
    common_words: list[str]
    pools: dict[Stratum, int]
    drawn: dict[Stratum, int]
    pairs: list[DrawnPair] = Field(default_factory=list[DrawnPair])


def sample(
    question: str, seed: int, model: str, proposal: str, strings: int, result: Draw
) -> PairSample:
    return PairSample(
        question=question,
        seed=seed,
        model=model,
        proposal=proposal,
        strings=strings,
        common_words=result.common_words,
        pools=result.pools,
        drawn={s: sum(p.stratum is s for p in result.pairs) for s in Stratum},
        pairs=result.pairs,
    )


def write_sample(path: Path, drawn: PairSample) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, (drawn.model_dump_json(indent=2) + "\n").encode())


def load_sample(path: Path) -> PairSample:
    return PairSample.model_validate_json(path.read_text())
