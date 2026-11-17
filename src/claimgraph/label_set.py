"""The eval pairs to label, chosen from the arms' answers as the pre-registration fixes them.

Every pair on which any two arms differ on the verdict or the primary reason is a disagreement
pair. At most 50 of them are labeled, drawn with a seed in proportion to the strata, and each
keeps its chance of being drawn so the estimates can weight it. Pairs where every arm agrees add
nothing to a paired difference, so only 20 are labeled, for the absolute rates; they are kept in
one seeded order, so later labels extend the same list. The set is written once and committed,
and the labeling page reads it, never the arms' answers.
"""

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import Field, model_validator

from claimgraph.models import Arm, NonBlank, PairClassification, Record
from claimgraph.pair_sample import DrawnPair, PairSample

LABEL_SET_FILE = "contradiction_label_set.json"
DISAGREEMENT_CAP = 50
AGREEMENT_LABELS = 20


class DisagreementPair(Record):
    pair_id: NonBlank
    # Outcome, pair kind and designs, the draw's stratum.
    stratum: NonBlank
    # The chance this pair was drawn: its stratum's slots over its stratum's disagreement pairs.
    inclusion: float = Field(gt=0, le=1)


class LabelSet(Record):
    question: NonBlank
    seed: int
    arms: list[Arm]
    # Every disagreement pair, so the estimates know each stratum's size.
    disagreement_strata: dict[str, int]
    disagreement: list[DisagreementPair]
    # Every agreement pair, in the seeded order labels follow.
    agreement_order: list[NonBlank]
    agreement_labeled: int

    @model_validator(mode="after")
    def _consistent(self) -> "LabelSet":
        drawn = [p.pair_id for p in self.disagreement]
        if len(set(drawn) | set(self.agreement_order)) != len(drawn) + len(self.agreement_order):
            raise ValueError("a pair is in one group, once")
        if self.agreement_labeled > len(self.agreement_order):
            raise ValueError("more agreement pairs labeled than there are")
        return self

    @property
    def pair_ids(self) -> list[str]:
        """The pairs to label: the drawn disagreement pairs, then the first agreement pairs."""
        drawn = [p.pair_id for p in self.disagreement]
        return drawn + self.agreement_order[: self.agreement_labeled]


def stratum(pair: DrawnPair) -> str:
    return f"{pair.outcome} | {pair.kind.value} | {pair.combination.value}"


def disagrees(answers: Sequence[PairClassification]) -> bool:
    return len({(a.verdict, a.reason) for a in answers}) > 1


def proportional(sizes: Mapping[str, int], total: int) -> dict[str, int]:
    """`total` slots shared in proportion to `sizes`, by largest remainder, ties by name."""
    whole = sum(sizes.values())
    if whole <= total:
        return dict(sizes)
    quotas = {s: total * n / whole for s, n in sizes.items()}
    slots = {s: int(q) for s, q in quotas.items()}
    left = total - sum(slots.values())
    for s in sorted(quotas, key=lambda s: (-(quotas[s] - slots[s]), s))[:left]:
        slots[s] += 1
    return slots


def build(
    sample: PairSample,
    answers: Mapping[Arm, Mapping[str, PairClassification]],
    seed: int,
    cap: int = DISAGREEMENT_CAP,
    agreement: int = AGREEMENT_LABELS,
) -> LabelSet:
    """The label set for the eval pairs, from every arm's answer on each."""
    pairs = sorted(sample.eval, key=lambda p: p.pair_id)
    missing = [(a, p.pair_id) for a in answers for p in pairs if p.pair_id not in answers[a]]
    if missing:
        arm, pair_id = missing[0]
        raise ValueError(f"{arm.value} has no answer for {pair_id}")
    rng = random.Random(seed)
    by_stratum: dict[str, list[DrawnPair]] = defaultdict(list)
    agreeing: list[str] = []
    for pair in pairs:
        if disagrees([answers[a][pair.pair_id] for a in answers]):
            by_stratum[stratum(pair)].append(pair)
        else:
            agreeing.append(pair.pair_id)
    sizes = {s: len(ps) for s, ps in sorted(by_stratum.items())}
    slots = proportional(sizes, cap)
    drawn: list[DisagreementPair] = []
    for name in sorted(by_stratum):
        chosen = rng.sample(by_stratum[name], slots[name])
        drawn += [
            DisagreementPair(pair_id=p.pair_id, stratum=name, inclusion=slots[name] / sizes[name])
            for p in sorted(chosen, key=lambda p: p.pair_id)
        ]
    rng.shuffle(agreeing)
    return LabelSet(
        question=sample.question,
        seed=seed,
        arms=list(answers),
        disagreement_strata=sizes,
        disagreement=drawn,
        agreement_order=agreeing,
        agreement_labeled=min(agreement, len(agreeing)),
    )


def write(path: Path, label_set: LabelSet) -> None:
    path.write_text(label_set.model_dump_json(indent=1) + "\n")


def read(path: Path) -> LabelSet:
    return LabelSet.model_validate_json(path.read_text())


def to_label(sample: PairSample, out_dir: Path) -> list[DrawnPair]:
    """The eval pairs the labeler labels: the label set's when there is one, else every one."""
    path = out_dir / LABEL_SET_FILE
    if not path.exists():
        return list(sample.eval)
    wanted = set(read(path).pair_ids)
    return [p for p in sample.eval if p.pair_id in wanted]
