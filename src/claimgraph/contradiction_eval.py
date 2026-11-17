"""Scoring the arms on the labeled contradiction pairs, as the pre-registration fixes it.

Every estimate is a weighted share over the labeled pairs on the question. Each group of the
label set, a disagreement stratum or the agreement pairs, stands for all of its pairs: a labeled
pair weighs its group's pairs over its group's labeled pairs, which for a disagreement pair is
the inverse of its chance of being drawn. A pair labeled off_question is left out of every score
and counted. A pair the caller leaves out, as the sensitivity check does, leaves its group's
count too, so the weights stay exact.

Intervals are percentile bootstraps with the label set's seed: the labeled pairs are resampled
within their groups. A paired difference resamples the disagreement pairs only, since on an
agreement pair every arm is equally right or wrong.
"""

import random
import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from operator import mul
from pathlib import Path

from pydantic import Field, field_validator

from claimgraph.label_set import LabelSet, stratum
from claimgraph.labels import ContradictionLabel
from claimgraph.models import Arm, NonBlank, PairClassification, Reason, Record, Verdict
from claimgraph.pair_sample import DrawnPair

BOOTSTRAP_ROUNDS = 10_000
AGREEMENT = "agreement"
OFF_QUESTION = Verdict.OFF_QUESTION.value
# The README block that eval contradictions --readme writes.
README_BLOCK = "contradiction-eval"
CORRECTIONS_FILE = "contradiction_corrections.toml"

Rule = Callable[[PairClassification, ContradictionLabel], bool]


def _correct(answer: PairClassification, label: ContradictionLabel) -> bool:
    return answer.verdict == label.verdict and answer.reason == label.reason


def _lenient_reason(answer: PairClassification, label: ContradictionLabel) -> bool:
    if answer.reason is None:
        return False
    return answer.reason == label.reason or answer.reason in label.also_differs


def _correct_lenient(answer: PairClassification, label: ContradictionLabel) -> bool:
    return answer.verdict == label.verdict and _lenient_reason(answer, label)


def _verdict(answer: PairClassification, label: ContradictionLabel) -> bool:
    return answer.verdict == label.verdict


# Correct, the verdict with the exact primary reason, is the pre-registered figure the paired
# differences use; the rest sit beside it.
RULES: dict[str, Rule] = {
    "verdict": _verdict,
    "correct": _correct,
    "correct_lenient": _correct_lenient,
}


@dataclass(frozen=True)
class Unit:
    """One labeled pair as the estimates see it."""

    pair_id: str
    # Its label-set group: a disagreement stratum, or AGREEMENT.
    group: str
    # The draw's stratum, for the per-stratum rows.
    stratum: str
    weight: float
    label: ContradictionLabel

    @property
    def scored(self) -> bool:
        return self.label.verdict is not Verdict.OFF_QUESTION


class Interval(Record):
    estimate: float | None
    low: float | None
    high: float | None

    @property
    def includes_zero(self) -> bool:
        return self.low is not None and self.high is not None and self.low <= 0 <= self.high


class ArmScore(Record):
    arm: Arm
    verdict: Interval
    correct: Interval
    correct_lenient: Interval
    precision_genuine: Interval
    # Of the pairs in scope, how many the arm called off_question.
    called_off_question: int
    usd_per_pair: float
    billed_per_pair: float


class Difference(Record):
    arm: Arm
    against: Arm
    difference: Interval


class StratumRow(Record):
    stratum: str
    pairs: int
    labeled: int
    scored: int
    correct: dict[Arm, float | None]


class ScopePick(Record):
    best: Arm
    gain_over_small: Interval
    arm: Arm
    # True when every candidate pair goes to `arm`; else only the top pairs per outcome do.
    every_pair: bool


class Figures(Record):
    """One full set of figures, over the pairs in scope."""

    pairs: int
    labeled: int
    off_question_labels: int
    scored: int
    # The estimated number of pairs in scope that are on the question.
    on_question: float
    arms: list[ArmScore]
    against_small: list[Difference]
    against_best: list[Difference]
    scope: ScopePick
    # Per arm, estimated pairs by the label's reason and the arm's reason or off_question.
    confusion: dict[Arm, dict[str, dict[str, float]]]
    strata: list[StratumRow]


def units(
    eval_pairs: Sequence[DrawnPair],
    found: LabelSet,
    labels: Sequence[ContradictionLabel],
    left_out: Collection[str] = (),
) -> tuple[list[Unit], dict[str, int]]:
    """The labeled pairs with their weights, and each group's size, without `left_out`."""
    by_id = {p.pair_id: p for p in eval_pairs}
    labeled = {label.pair_id: label for label in labels}
    missing = [p for p in found.pair_ids if p not in labeled]
    if missing:
        raise ValueError(f"{len(missing)} pairs of the label set are not labeled: {missing[0]}")
    agreeing = set(found.agreement_order)
    group_of = {p.pair_id: AGREEMENT if p.pair_id in agreeing else stratum(p) for p in eval_pairs}
    sizes = Counter(g for p, g in group_of.items() if group_of[p] != AGREEMENT)
    if dict(sizes) != found.disagreement_strata:
        raise ValueError("the label set's strata do not match the eval pairs")
    pairs = Counter(g for p, g in group_of.items() if p not in left_out)
    chosen = [p for p in found.pair_ids if p not in left_out]
    drawn = Counter(group_of[p] for p in chosen)
    made = [
        Unit(
            pair_id=p,
            group=group_of[p],
            stratum=stratum(by_id[p]),
            weight=pairs[group_of[p]] / drawn[group_of[p]],
            label=labeled[p],
        )
        for p in chosen
    ]
    return made, dict(pairs)


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _interval(estimate: float | None, draws: list[float]) -> Interval:
    if estimate is None or not draws:
        return Interval(estimate=estimate, low=None, high=None)
    return Interval(estimate=estimate, low=_quantile(draws, 0.025), high=_quantile(draws, 0.975))


def _resample(rng: random.Random, groups: Mapping[str, list[int]], counts: list[int]) -> None:
    """Add one within-group resample of the units, as multiplicities, to `counts`."""
    for g in sorted(groups):
        for i in rng.choices(groups[g], k=len(groups[g])):
            counts[i] += 1


Key = tuple[Arm, str]


def score(
    eval_pairs: Sequence[DrawnPair],
    found: LabelSet,
    labels: Sequence[ContradictionLabel],
    answers: Mapping[Arm, Mapping[str, PairClassification]],
    costs: Mapping[Arm, tuple[float, float]],
    left_out: Collection[str] = (),
    rounds: int = BOOTSTRAP_ROUNDS,
) -> Figures:
    """Every arm's figures; `costs` gives each arm's dollars and billed tokens per pair."""
    sample, pairs = units(eval_pairs, found, labels, left_out)
    arms = list(answers)
    # Each figure is a ratio of weighted sums over the units, so a resample only reweights them.
    num: dict[Key, list[float]] = {}
    den: dict[Key, list[float]] = {}
    for arm in arms:
        given = [answers[arm][u.pair_id] for u in sample]
        for name, rule in RULES.items():
            den[(arm, name)] = [u.weight if u.scored else 0.0 for u in sample]
            num[(arm, name)] = [
                u.weight if u.scored and rule(a, u.label) else 0.0
                for u, a in zip(sample, given, strict=True)
            ]
        called = [
            u.scored and a.verdict is Verdict.GENUINE for u, a in zip(sample, given, strict=True)
        ]
        den[(arm, "precision_genuine")] = [
            u.weight if c else 0.0 for u, c in zip(sample, called, strict=True)
        ]
        num[(arm, "precision_genuine")] = [
            u.weight if c and u.label.verdict is Verdict.GENUINE else 0.0
            for u, c in zip(sample, called, strict=True)
        ]

    def figures(counts: list[int]) -> dict[Key, float | None]:
        out: dict[Key, float | None] = {}
        for key in num:
            whole = sum(map(mul, counts, den[key]))
            out[key] = sum(map(mul, counts, num[key])) / whole if whole else None
        return out

    point = figures([1] * len(sample))
    disagreement: dict[str, list[int]] = defaultdict(list)
    agreement: list[int] = []
    for i, u in enumerate(sample):
        (agreement if u.group == AGREEMENT else disagreement[u.group]).append(i)
    rng = random.Random(found.seed)
    draws: dict[Key, list[float]] = defaultdict(list)
    paired: dict[tuple[Arm, Arm], list[float]] = defaultdict(list)
    for _ in range(rounds):
        moved = [0] * len(sample)
        _resample(rng, disagreement, moved)
        held = list(moved)
        for i in agreement:
            held[i] += 1
        again = list(moved)
        for i in rng.choices(agreement, k=len(agreement)) if agreement else []:
            again[i] += 1
        for key, value in figures(again).items():
            if value is not None:
                draws[key].append(value)
        kept = figures(held)
        for a in arms:
            for b in arms:
                x, y = kept[(a, "correct")], kept[(b, "correct")]
                if a is not b and x is not None and y is not None:
                    paired[(a, b)].append(x - y)

    def difference(a: Arm, b: Arm) -> Difference:
        if a is b:
            return Difference(arm=a, against=b, difference=Interval(estimate=0, low=0, high=0))
        x, y = point[(a, "correct")], point[(b, "correct")]
        estimate = x - y if x is not None and y is not None else None
        return Difference(arm=a, against=b, difference=_interval(estimate, paired[(a, b)]))

    in_scope = [p for p in eval_pairs if p.pair_id not in left_out]
    scores = [
        ArmScore(
            arm=arm,
            correct=_interval(point[(arm, "correct")], draws[(arm, "correct")]),
            correct_lenient=_interval(
                point[(arm, "correct_lenient")], draws[(arm, "correct_lenient")]
            ),
            verdict=_interval(point[(arm, "verdict")], draws[(arm, "verdict")]),
            precision_genuine=_interval(
                point[(arm, "precision_genuine")], draws[(arm, "precision_genuine")]
            ),
            called_off_question=sum(
                answers[arm][p.pair_id].verdict is Verdict.OFF_QUESTION for p in in_scope
            ),
            usd_per_pair=costs[arm][0],
            billed_per_pair=costs[arm][1],
        )
        for arm in arms
    ]
    best = max(scores, key=lambda s: (s.correct.estimate or 0.0, -s.usd_per_pair)).arm
    against_best = [difference(arm, best) for arm in arms]
    return Figures(
        pairs=sum(pairs.values()),
        labeled=len(sample),
        off_question_labels=sum(not u.scored for u in sample),
        scored=sum(u.scored for u in sample),
        on_question=sum(u.weight for u in sample if u.scored),
        arms=scores,
        against_small=[difference(arm, Arm.SINGLE_SMALL) for arm in arms],
        against_best=against_best,
        scope=scope_pick(scores, best, difference(best, Arm.SINGLE_SMALL), against_best),
        confusion={arm: _confusion(sample, answers[arm]) for arm in arms},
        strata=_strata(in_scope, sample, answers),
    )


def scope_pick(
    scores: Sequence[ArmScore], best: Arm, gain: Difference, against_best: Sequence[Difference]
) -> ScopePick:
    """The pre-registered scope rule: single-small for every pair unless the best arm's gain
    over it is clear; else the cheapest arm not clearly worse than the best, for the top pairs.
    """
    if best is Arm.SINGLE_SMALL or gain.difference.includes_zero:
        return ScopePick(
            best=best, gain_over_small=gain.difference, arm=Arm.SINGLE_SMALL, every_pair=True
        )
    cost = {s.arm: s.usd_per_pair for s in scores}
    close = [d.arm for d in against_best if d.difference.includes_zero]
    return ScopePick(
        best=best,
        gain_over_small=gain.difference,
        arm=min(close, key=lambda a: cost[a]),
        every_pair=False,
    )


def _confusion(
    sample: Sequence[Unit], answers: Mapping[str, PairClassification]
) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for u in sample:
        if not u.scored or u.label.reason is None:
            continue
        given = answers[u.pair_id].reason
        table[u.label.reason.value][given.value if given else OFF_QUESTION] += u.weight
    return {row: dict(cols) for row, cols in sorted(table.items())}


def _accuracy(sample: Sequence[Unit], answers: Mapping[str, PairClassification]) -> float | None:
    whole = sum(u.weight for u in sample if u.scored)
    right = sum(u.weight for u in sample if u.scored and _correct(answers[u.pair_id], u.label))
    return right / whole if whole else None


def _strata(
    in_scope: Sequence[DrawnPair],
    sample: Sequence[Unit],
    answers: Mapping[Arm, Mapping[str, PairClassification]],
) -> list[StratumRow]:
    pairs = Counter(stratum(p) for p in in_scope)
    rows: list[StratumRow] = []
    for name in sorted(pairs):
        mine = [u for u in sample if u.stratum == name]
        rows.append(
            StratumRow(
                stratum=name,
                pairs=pairs[name],
                labeled=len(mine),
                scored=sum(u.scored for u in mine),
                correct={arm: _accuracy(mine, answers[arm]) for arm in answers},
            )
        )
    return rows


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _share_with(interval: Interval) -> str:
    if interval.low is None or interval.high is None:
        return _pct(interval.estimate)
    return f"{_pct(interval.estimate)} ({interval.low:.0%} to {interval.high:.0%})"


def _points(interval: Interval) -> str:
    def signed(v: float) -> str:
        return f"{v * 100:+.1f}"

    if interval.estimate is None:
        return "n/a"
    if interval.low is None or interval.high is None:
        return signed(interval.estimate)
    return f"{signed(interval.estimate)} ({signed(interval.low)} to {signed(interval.high)})"


def scope_line(figures: Figures) -> str:
    pick = figures.scope
    gain = _points(pick.gain_over_small)
    if pick.every_pair:
        return (
            f"Scope rule: the best arm is {pick.best.value}, and its gain over single-small, "
            f"{gain} points, has an interval that includes zero, so every candidate pair is "
            "classified at single-small."
        )
    return (
        f"Scope rule: the best arm is {pick.best.value}, {gain} points over single-small, so the "
        f"top pairs per outcome are classified at {pick.arm.value}, the cheapest arm whose "
        "interval against the best includes zero."
    )


def tables(figures: Figures) -> str:
    """The arms table, the paired differences and the scope rule's pick, for one set of pairs."""
    lines = [
        "| Arm | Precision on genuine | Verdict | Reason, strict | Reason, lenient "
        "| Called off question | Cost per pair | Billed tokens per pair |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for a in figures.arms:
        lines.append(
            f"| {a.arm.value} | {_share_with(a.precision_genuine)} | {_share_with(a.verdict)} "
            f"| {_share_with(a.correct)} | {_share_with(a.correct_lenient)} "
            f"| {a.called_off_question} | ${a.usd_per_pair:.4f} | {a.billed_per_pair:,.0f} |"
        )
    best = figures.scope.best.value
    lines += [
        "",
        f"Paired differences in reason accuracy, strict, in points: against single-small and "
        f"against {best}, the best arm.",
        "",
        f"| Arm | Against single-small | Against {best} |",
        "| --- | ---: | ---: |",
    ]
    for small, top in zip(figures.against_small, figures.against_best, strict=True):
        cells = ["—" if d.arm is d.against else _points(d.difference) for d in (small, top)]
        lines.append(f"| {small.arm.value} | {cells[0]} | {cells[1]} |")
    lines += ["", scope_line(figures)]
    return "\n".join(lines) + "\n"


def details(figures: Figures) -> str:
    """The confusion matrices and the per-stratum rows, folded away."""
    reasons = sorted({r for t in figures.confusion.values() for row in t.values() for r in row})
    rows = sorted({r for t in figures.confusion.values() for r in t})
    lines = [
        "<details><summary>Reasons by arm, and accuracy by stratum</summary>",
        "",
        "Estimated pairs on the question, by the label's reason (rows) and the arm's (columns).",
        "",
    ]
    for arm, table in figures.confusion.items():
        lines += [f"**{arm.value}**", "", "| Label | " + " | ".join(reasons) + " |"]
        lines.append("| --- |" + " ---: |" * len(reasons))
        for row in rows:
            cells = [f"{table.get(row, {}).get(r, 0.0):.0f}" for r in reasons]
            lines.append(f"| {row} | " + " | ".join(cells) + " |")
        lines.append("")
    arms = list(figures.confusion)
    lines += [
        "Correct, strict, by stratum of the draw; point estimates only, from few labels.",
        "",
        "| Stratum | Pairs | Labeled | Scored | " + " | ".join(a.value for a in arms) + " |",
        "| --- | ---: | ---: | ---: |" + " ---: |" * len(arms),
    ]
    for row in figures.strata:
        cells = [_pct(row.correct[a]) for a in arms]
        lines.append(
            f"| {row.stratum.replace(' | ', ', ')} | {row.pairs} | {row.labeled} | {row.scored} | "
            + " | ".join(cells)
            + " |"
        )
    lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


class Found(StrEnum):
    BEFORE_SCORING = "before_scoring"
    AFTER_SCORING = "after_scoring"


class Correction(Record):
    """One accepted change to a contradiction label, and what it was."""

    outcome: NonBlank
    study_a: NonBlank
    study_b: NonBlank
    was_verdict: Verdict
    was_reason: Reason | None
    was_also_differs: list[Reason]
    found: Found
    rule: NonBlank

    @field_validator("was_reason", mode="before")
    @classmethod
    def _blank_reason_is_absent(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def pair_id(self) -> str:
        return f"{self.outcome}|{self.study_a}|{self.study_b}"


class _Corrections(Record):
    correction: list[Correction] = Field(default_factory=list[Correction])


def load_corrections(path: Path) -> list[Correction]:
    if not path.exists():
        return []
    return _Corrections.model_validate(tomllib.loads(path.read_text())).correction


def before(
    labels: Sequence[ContradictionLabel], corrections: Sequence[Correction], found: Found
) -> list[ContradictionLabel]:
    """The labels as they were before the corrections found at `found`."""
    undo = {c.pair_id: c for c in corrections if c.found is found}
    return [
        label.model_copy(
            update={
                "verdict": undo[label.pair_id].was_verdict,
                "reason": undo[label.pair_id].was_reason,
                "also_differs": undo[label.pair_id].was_also_differs,
            }
        )
        if label.pair_id in undo
        else label
        for label in labels
    ]


class HostedPick(Record):
    arm: Arm
    every_pair: bool
    why: NonBlank


def hosted_pick(primary: Figures, sensitivity: Figures) -> HostedPick:
    """The arm the hosted page classifies at.

    When the two figures agree, their pick. When they don't, the owner's ruling: single-small
    for every pair, unless its precision on genuine without the post-hoc pairs is clearly below
    the primary pick's, their intervals not overlapping, and then the primary pick.
    """
    if primary.scope.arm == sensitivity.scope.arm:
        return HostedPick(
            arm=primary.scope.arm,
            every_pair=primary.scope.every_pair,
            why="both figures pick it",
        )
    by_arm = {a.arm: a.precision_genuine for a in sensitivity.arms}
    small, picked = by_arm[Arm.SINGLE_SMALL], by_arm[primary.scope.arm]
    clearly_below = small.high is not None and picked.low is not None and small.high < picked.low
    shown = (
        f"without those pairs, single-small's precision on genuine is {_share_with(small)} and "
        f"{primary.scope.arm.value}'s {_share_with(picked)}"
    )
    if clearly_below:
        return HostedPick(
            arm=primary.scope.arm,
            every_pair=primary.scope.every_pair,
            why=f"{shown}; the intervals do not overlap",
        )
    return HostedPick(arm=Arm.SINGLE_SMALL, every_pair=True, why=f"{shown}; the intervals overlap")


def readme(
    primary: Figures,
    sensitivity: Figures,
    earlier: Figures,
    corrected: int,
    left_out: int,
    rounds: int = BOOTSTRAP_ROUNDS,
) -> str:
    """The README block: the pre-registered figures, the post-hoc check, and the ruling."""
    lines = [
        f"Six arms on the {primary.pairs} eval pairs, scored from the committed recordings "
        f"against {primary.labeled} hand labels: the drawn disagreement pairs, weighted by the "
        "inverse of their chance of being drawn, and the first agreement pairs, weighted for "
        f"the rest. {primary.off_question_labels} labeled pairs are off the question and left "
        f"out, so the figures cover an estimated {primary.on_question:.0f} pairs on it. "
        "Precision on genuine and verdict accuracy judge the verdict alone. Reason accuracy, "
        "strict, needs the verdict and the label's primary reason exactly; lenient also accepts "
        "a reason the label lists under also_differs. Strict rates are low because the labeled "
        "pairs are the hardest ones, the disagreements, weighted up to stand for their strata, "
        f"and an exact reason match is required. 95% intervals from {rounds:,} bootstrap rounds.",
        "",
        tables(primary).rstrip("\n"),
        "",
        f"Reason accuracy before and after the {corrected} corrections made after scoring. Each "
        "applied a written rule on a pair where every arm agreed, so it moves every arm alike "
        "and no paired difference.",
        "",
        "| Arm | Strict, before | Strict, after | Lenient, before | Lenient, after |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for old, new in zip(earlier.arms, primary.arms, strict=True):
        lines.append(
            f"| {new.arm.value} | {_pct(old.correct.estimate)} | {_pct(new.correct.estimate)} "
            f"| {_pct(old.correct_lenient.estimate)} | {_pct(new.correct_lenient.estimate)} |"
        )
    lines += [
        "",
        details(primary).rstrip("\n"),
        "",
        f"**Post-hoc check, not pre-registered:** the same figures without the {left_out} eval "
        "pairs drawn before the 15D polarity override. Those pairs test whether an arm catches "
        "a polarity error, read then as harm against a benefit, that the corpus no longer has. "
        f"{sensitivity.labeled} labels remain, {sensitivity.off_question_labels} of them off "
        f"the question, covering an estimated {sensitivity.on_question:.0f} pairs.",
        "",
        tables(sensitivity).rstrip("\n"),
    ]
    pick = hosted_pick(primary, sensitivity)
    scope = "every candidate pair" if pick.every_pair else "the top pairs per outcome"
    if primary.scope.arm != sensitivity.scope.arm:
        lines += [
            "",
            f"**Ruling:** the two figures pick different arms, {primary.scope.arm.value} on the "
            f"pre-registered figure and {sensitivity.scope.arm.value} without those pairs. The "
            "pre-registered figure picks it only because of the 15D pairs, a data error the "
            "corpus no longer has, and without them no arm separates from single-small. So the "
            "owner ruled: single-small for every pair, unless its precision on genuine without "
            f"those pairs is clearly below {primary.scope.arm.value}'s. {pick.why[0].upper()}"
            f"{pick.why[1:]}.",
        ]
    lines += ["", f"The hosted page classifies {scope} at {pick.arm.value}."]
    return "\n".join(lines) + "\n"
