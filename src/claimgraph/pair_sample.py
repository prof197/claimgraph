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
from claimgraph.config import NOT_REPORTED
from claimgraph.measures import same_measure
from claimgraph.models import (
    REVIEW_DESIGNS,
    CandidatePair,
    Design,
    Finding,
    FindingId,
    NonBlank,
    PairKind,
    Record,
)

SAMPLE_FILE = "contradiction_sample.json"
ATTRIBUTION_FILE = "CONTRADICTION_ATTRIBUTION.md"
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


def panel_order(
    pairs: Sequence[DrawnPair], studies: Mapping[str, tuple[float | None, int | None]]
) -> list[DrawnPair]:
    """The pairs in the contradiction panel's order: the two studies' design weights added,
    then their sample sizes added, larger first, a pair missing a size last, then pair_id."""

    def key(p: DrawnPair) -> tuple[float, int, int, str]:
        (wa, na), (wb, nb) = studies[p.study_a], studies[p.study_b]
        size = None if na is None or nb is None else na + nb
        return (-((wa or 0) + (wb or 0)), size is None, -(size or 0), p.pair_id)

    return sorted(pairs, key=key)


# Two sample sizes are alike within this factor of each other.
ALIKE_SIZE_RATIO = 3


def shared_fields(
    a: Finding,
    b: Finding,
    designs: tuple[Design, Design],
    sizes: tuple[int | None, int | None],
    dimensions: Sequence[str],
) -> int:
    """How many ways two findings' studies are alike: the same design, the same outcome measure,
    the same value of each custom dimension, and sample sizes within ALIKE_SIZE_RATIO. A field
    counts only when both studies report it: two unreported values are unknown, not alike."""
    design_a, design_b = designs
    same_design = design_a == design_b and design_a is not Design.OTHER
    n_a, n_b = sizes
    same_size = (
        n_a is not None and n_b is not None and max(n_a, n_b) <= ALIKE_SIZE_RATIO * min(n_a, n_b)
    )
    same_values = 0
    for name in dimensions:
        value_a, value_b = a.dimensions.get(name), b.dimensions.get(name)
        if value_a not in (None, NOT_REPORTED) and value_a == value_b:
            same_values += 1
    return (
        same_design + same_measure(a.outcome_reported, b.outcome_reported) + same_values + same_size
    )


def alike_pairs(
    pairs: Sequence[DrawnPair],
    findings: Mapping[str, Finding],
    designs: Mapping[str, Design],
    studies: Mapping[str, tuple[float | None, int | None]],
    dimensions: Sequence[str],
    minimum: int,
    per_study: int | None = None,
) -> list[DrawnPair]:
    """The pairs whose studies are alike in at least `minimum` ways, most alike first, then in
    the panel's order; with `per_study`, no study in more pairs than that, each keeping its
    most alike. Ranking by weight alone fills the panel with one huge trial against everything,
    where a disagreement is explained by design or size before anyone reads it.

    `designs` is keyed by PMID, for the paper each finding came from; `studies` gives each
    study's design weight and sample size by study key, as the page shows them.
    """
    scored: list[tuple[int, DrawnPair]] = []
    for pair in pairs:
        a, b = findings[pair.finding_a], findings[pair.finding_b]
        (_, n_a), (_, n_b) = studies[pair.study_a], studies[pair.study_b]
        shared = shared_fields(a, b, (designs[a.pmid], designs[b.pmid]), (n_a, n_b), dimensions)
        if shared >= minimum:
            scored.append((shared, pair))
    rank = {p.pair_id: i for i, p in enumerate(panel_order([p for _, p in scored], studies))}
    scored.sort(key=lambda sp: (-sp[0], rank[sp[1].pair_id]))
    used: dict[str, int] = defaultdict(int)
    chosen: list[DrawnPair] = []
    for _, pair in scored:
        if per_study is not None and max(used[pair.study_a], used[pair.study_b]) >= per_study:
            continue
        used[pair.study_a] += 1
        used[pair.study_b] += 1
        chosen.append(pair)
    return chosen


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


def eval_papers(sample: PairSample) -> set[str]:
    """The PMIDs of the eval pairs' headline papers, the ones their recordings quote."""
    return {_pmid(f) for p in sample.eval for f in (p.finding_a, p.finding_b)}
