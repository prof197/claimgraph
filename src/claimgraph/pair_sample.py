"""The drawn contradiction pairs: the eval's pairs and the dev pairs, in one committed file.

A drawn pair names the two counted findings it compares, so a labeling page or a classifier
reads both findings' fields from the extraction without redoing the comparison.
"""

import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import model_validator

from claimgraph.compare import Candidates
from claimgraph.models import (
    REVIEW_DESIGNS,
    CandidatePair,
    Design,
    FindingId,
    NonBlank,
    PairKind,
    Record,
)

SAMPLE_FILE = "contradiction_sample.json"
SEED = 20261115
# Benefit against harm is about one candidate pair in twenty, so the eval oversamples it to a
# third. Dev keeps the same split.
EVAL_SIZES = {PairKind.BENEFIT_VS_HARM: 100, PairKind.EFFECT_VS_NONE: 200}
DEV_SIZES = {PairKind.BENEFIT_VS_HARM: 13, PairKind.EFFECT_VS_NONE: 27}

TRIALS = frozenset({Design.RCT, Design.NON_RANDOMIZED_TRIAL})
# Case series count as observational; other primary designs carry no design weight.
OBSERVATIONAL = frozenset(
    {Design.COHORT, Design.CASE_CONTROL, Design.CROSS_SECTIONAL, Design.CASE_SERIES}
)


class Combination(StrEnum):
    """The two studies' designs, a stratum of the draw."""

    TRIAL_TRIAL = "trial_trial"
    TRIAL_OBSERVATIONAL = "trial_observational"
    OBSERVATIONAL_OBSERVATIONAL = "observational_observational"
    WITH_OTHER = "with_other"


def combination(a: Design, b: Design) -> Combination:
    if a in REVIEW_DESIGNS or b in REVIEW_DESIGNS:
        raise ValueError("reviews are never paired")
    if a not in TRIALS | OBSERVATIONAL or b not in TRIALS | OBSERVATIONAL:
        return Combination.WITH_OTHER
    trials = (a in TRIALS) + (b in TRIALS)
    return [
        Combination.OBSERVATIONAL_OBSERVATIONAL,
        Combination.TRIAL_OBSERVATIONAL,
        Combination.TRIAL_TRIAL,
    ][trials]


class DrawnPair(CandidatePair):
    combination: Combination
    # The counted findings of study a and study b on the outcome.
    finding_a: FindingId
    finding_b: FindingId


class PairSample(Record):
    question: NonBlank
    seed: int
    # Labeled, classified by every arm and replayed in CI. Both studies openly licensed.
    eval: list[DrawnPair]
    # For tuning the classification prompt only; no study of a dev pair is in an eval pair.
    dev: list[DrawnPair]

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        ids = [p.pair_id for p in self.eval + self.dev]
        if len(set(ids)) != len(ids):
            raise ValueError("a pair is drawn once")
        studies = {s for p in self.eval for s in (p.study_a, p.study_b)}
        if any(s in studies for p in self.dev for s in (p.study_a, p.study_b)):
            raise ValueError("dev pairs share no study with eval pairs")
        return self


def read_sample(path: Path) -> PairSample:
    return PairSample.model_validate_json(path.read_text())


Cell = tuple[str, PairKind, Combination]


def cell(pair: DrawnPair) -> Cell:
    return (pair.outcome, pair.kind, pair.combination)


def _weight(c: Cell) -> float:
    # Pairs with another design carry no design weight and sort last on the page's panel, so
    # they get half the share of the other combinations.
    return 0.5 if c[2] is Combination.WITH_OTHER else 1.0


def allocate(available: Mapping[Cell, int], sizes: Mapping[PairKind, int]) -> dict[Cell, int]:
    """Slots per cell: within each pair kind, shared equally by weight, up to what each cell
    holds; a cell that runs out frees its slots for the rest."""
    slots = dict.fromkeys(available, 0)
    for kind, size in sizes.items():
        cells = sorted(c for c in available if c[1] is kind)
        for _ in range(size):
            open_cells = [c for c in cells if slots[c] < available[c]]
            if not open_cells:
                break
            pick = min(open_cells, key=lambda c: (slots[c] / _weight(c), c))
            slots[pick] += 1
    return slots


def drawn_pairs(found: Candidates, designs: Mapping[str, Design]) -> list[DrawnPair]:
    """Every candidate pair with its combination and the two headline findings it compares;
    `designs` maps each headline finding's PMID to its study's design."""
    lead = {(h.outcome, h.study): h for h in found.headlines}
    pairs: list[DrawnPair] = []
    for p in found.pairs:
        a, b = lead[(p.outcome, p.study_a)], lead[(p.outcome, p.study_b)]
        pairs.append(
            DrawnPair(
                outcome=p.outcome,
                study_a=p.study_a,
                study_b=p.study_b,
                kind=p.kind,
                combination=combination(designs[a.pmid], designs[b.pmid]),
                finding_a=a.finding_id,
                finding_b=b.finding_id,
            )
        )
    return pairs


def _take(
    pool: Sequence[DrawnPair], sizes: Mapping[PairKind, int], rng: random.Random
) -> list[DrawnPair]:
    by_cell: dict[Cell, list[DrawnPair]] = defaultdict(list)
    for pair in sorted(pool, key=lambda p: p.pair_id):
        by_cell[cell(pair)].append(pair)
    for pairs in by_cell.values():
        rng.shuffle(pairs)
    slots = allocate({c: len(v) for c, v in by_cell.items()}, sizes)
    return sorted((p for c, n in slots.items() for p in by_cell[c][:n]), key=lambda p: p.pair_id)


def licensed(pairs: Sequence[DrawnPair], is_open: Callable[[str], bool]) -> list[DrawnPair]:
    """The pairs whose two headline papers are openly licensed; `is_open` takes a PMID."""
    return [p for p in pairs if is_open(_pmid(p.finding_a)) and is_open(_pmid(p.finding_b))]


def _pmid(finding_id: str) -> str:
    return finding_id.rsplit("-", 1)[0]


def draw(
    question: str,
    pairs: Sequence[DrawnPair],
    is_open: Callable[[str], bool],
    seed: int = SEED,
) -> PairSample:
    """The eval pairs from pairs whose two headline papers are openly licensed, then the dev
    pairs, under any licence, from studies no eval pair touches."""
    rng = random.Random(seed)
    eval_pairs = _take(licensed(pairs, is_open), EVAL_SIZES, rng)
    used = {s for p in eval_pairs for s in (p.study_a, p.study_b)}
    rest = [p for p in pairs if p.study_a not in used and p.study_b not in used]
    dev_pairs = _take(rest, DEV_SIZES, rng)
    return PairSample(question=question, seed=seed, eval=eval_pairs, dev=dev_pairs)


def strata_table(
    pairs: Sequence[DrawnPair], licensed: Sequence[DrawnPair], sample: PairSample
) -> str:
    """Candidate pairs, openly licensed ones and drawn ones per stratum, as Markdown."""
    counts: dict[Cell, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for column, group in enumerate([pairs, licensed, sample.eval, sample.dev]):
        for pair in group:
            counts[cell(pair)][column] += 1
    lines = [
        "| Outcome | Pair kind | Designs | Candidates | Both open | Eval | Dev |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for (outcome, kind, combo), (n, open_n, drawn, dev) in sorted(counts.items()):
        lines.append(
            f"| {outcome} | {kind.value} | {combo.value} | {n:,} | {open_n:,} | {drawn} | {dev} |"
        )
    totals = [sum(c[i] for c in counts.values()) for i in range(4)]
    lines.append(f"| All | | | {totals[0]:,} | {totals[1]:,} | {totals[2]} | {totals[3]} |")
    return "\n".join(lines) + "\n"
