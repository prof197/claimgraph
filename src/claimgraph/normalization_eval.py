"""The normalization eval: the recorded proposal scored against the outcome kinds.

Every string has a kind, and two strings merge exactly when they share one, so merge precision
and recall are counted over every pair of in-domain strings. A string labeled out of domain
leaves all its pairs out and is counted. A string the proposal put under an outcome it marked
outside the domain merges with nothing, as review deletes that outcome. The drawn pairs are
reported as a subset, stratum by stratum with its pool, so the hard cases show; the strata come
from the proposal, so their rates are not rates over all pairs.
"""

from collections import Counter
from collections.abc import Collection, Sequence
from math import comb

from claimgraph.models import NormalizedOutcome, Record
from claimgraph.outcome_kinds import Kind, KindLabel
from claimgraph.outcome_pairs import PairSample, Stratum


class MergeCounts(Record):
    pairs: int
    labeled: int
    proposed: int
    both: int

    @property
    def precision(self) -> float | None:
        return self.both / self.proposed if self.proposed else None

    @property
    def recall(self) -> float | None:
        return self.both / self.labeled if self.labeled else None


class StratumScore(Record):
    stratum: Stratum
    pool: int
    drawn: int
    # Drawn pairs with a string labeled out of domain, left out of the counts.
    out_of_domain: int
    counts: MergeCounts


class NormalizationScores(Record):
    strings: int
    outcomes: int
    labeled_out: int
    proposed_out: int
    # Strings both labeled out of domain and put outside the domain by the proposal.
    both_out: int
    all_pairs: MergeCounts
    drawn: MergeCounts
    strata: list[StratumScore]


def score(
    labels: Sequence[KindLabel],
    outcomes: Sequence[NormalizedOutcome],
    outside: Collection[str],
    drawn: PairSample,
) -> NormalizationScores:
    kinds: dict[str, Kind] = {}
    for label in labels:
        if not isinstance(label.kind, Kind):
            raise ValueError(f"{label.string!r} has no kind yet")
        kinds[label.string] = label.kind
    group = {m: o.name for o in outcomes for m in o.members}
    if set(group) != set(kinds):
        raise ValueError("the proposal and the kind file list different strings")
    # The outcome a string is merged under, or None when the proposal put it outside the domain.
    merged = {s: None if g in outside else g for s, g in group.items()}
    inside = [s for s, k in kinds.items() if k is not Kind.OUT_OF_DOMAIN]

    def pairs_within(counts: Counter[object]) -> int:
        return sum(comb(n, 2) for n in counts.values())

    all_pairs = MergeCounts(
        pairs=comb(len(inside), 2),
        labeled=pairs_within(Counter(kinds[s] for s in inside)),
        proposed=pairs_within(Counter(merged[s] for s in inside if merged[s] is not None)),
        both=pairs_within(Counter((kinds[s], merged[s]) for s in inside if merged[s] is not None)),
    )

    def counted(pairs: Sequence[tuple[str, str]]) -> tuple[int, MergeCounts]:
        scored = [(a, b) for a, b in pairs if Kind.OUT_OF_DOMAIN not in (kinds[a], kinds[b])]
        gold = [kinds[a] == kinds[b] for a, b in scored]
        made = [merged[a] is not None and merged[a] == merged[b] for a, b in scored]
        return len(pairs) - len(scored), MergeCounts(
            pairs=len(scored),
            labeled=sum(gold),
            proposed=sum(made),
            both=sum(g and m for g, m in zip(gold, made, strict=True)),
        )

    missing = {s for p in drawn.pairs for s in (p.a, p.b)} - set(kinds)
    if missing:
        raise ValueError(f"{len(missing)} drawn strings are not in the kind file")
    strata: list[StratumScore] = []
    for s in Stratum:
        pairs = [(p.a, p.b) for p in drawn.pairs if p.stratum is s]
        out, counts = counted(pairs)
        strata.append(
            StratumScore(
                stratum=s,
                pool=drawn.pools[s],
                drawn=len(pairs),
                out_of_domain=out,
                counts=counts,
            )
        )
    labeled_out = {s for s, k in kinds.items() if k is Kind.OUT_OF_DOMAIN}
    proposed_out = {s for s, g in merged.items() if g is None}
    return NormalizationScores(
        strings=len(kinds),
        outcomes=len(outcomes),
        labeled_out=len(labeled_out),
        proposed_out=len(proposed_out),
        both_out=len(labeled_out & proposed_out),
        all_pairs=all_pairs,
        drawn=counted([(p.a, p.b) for p in drawn.pairs])[1],
        strata=strata,
    )


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _row(name: str, counts: MergeCounts, *lead: str) -> str:
    cells = [
        name,
        *lead,
        f"{counts.pairs:,}",
        f"{counts.labeled:,}",
        f"{counts.proposed:,}",
        f"{counts.both:,}",
        _rate(counts.precision),
        _rate(counts.recall),
    ]
    return "| " + " | ".join(cells) + " |"


def table(scores: NormalizationScores) -> str:
    """The scores as Markdown: all pairs and the drawn subset, then each stratum."""
    head = "Scored | Labeled merges | Proposed merges | Both | Merge precision | Merge recall"
    lines = [
        f"| Pairs | {head} |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        _row("Every pair of in-domain strings", scores.all_pairs),
        _row("Drawn pairs", scores.drawn),
        "",
        f"| Stratum | Pool | Drawn | Out of domain | {head} |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in scores.strata:
        lead = (f"{s.pool:,}", str(s.drawn), str(s.out_of_domain))
        lines.append(_row(s.stratum.value.replace("_", " "), s.counts, *lead))
    lines += [
        "",
        f"{scores.strings} strings, {scores.labeled_out} labeled out of domain and left out of "
        f"every pair. The proposal has {scores.outcomes} outcomes and put {scores.proposed_out} "
        f"strings outside the domain, {scores.both_out} of them labeled out of domain.",
    ]
    return "\n".join(lines) + "\n"
