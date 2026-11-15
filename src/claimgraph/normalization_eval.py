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
from pathlib import Path

from claimgraph import normalize, outcome_kinds
from claimgraph.config import QuestionConfig
from claimgraph.extract import ModelChoice
from claimgraph.llm import ReplayMiss, recorded_request
from claimgraph.models import NormalizedOutcome, Record
from claimgraph.normalize import Proposal
from claimgraph.outcome_kinds import (
    CORRECTIONS_FILE,
    Found,
    Kind,
    KindLabel,
    before,
    load_corrections,
)
from claimgraph.outcome_pairs import SAMPLE_FILE, PairSample, Stratum, load_sample


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


_HEAD = "Scored | Labeled merges | Proposed merges | Both | Merge precision | Merge recall"


def strata_table(scores: NormalizationScores) -> str:
    lines = [
        f"| Stratum | Pool | Drawn | Out of domain | {_HEAD} |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in scores.strata:
        lead = (f"{s.pool:,}", str(s.drawn), str(s.out_of_domain))
        lines.append(_row(s.stratum.value.replace("_", " "), s.counts, *lead))
    return "\n".join(lines) + "\n"


def footer(scores: NormalizationScores) -> str:
    return (
        f"{scores.strings} strings, {scores.labeled_out} labeled out of domain and left out of "
        f"every pair. The proposal has {scores.outcomes} outcomes and put {scores.proposed_out} "
        f"strings outside the domain, {scores.both_out} of them labeled out of domain.\n"
    )


def table(scores: NormalizationScores) -> str:
    """The scores as Markdown: all pairs and the drawn subset, then each stratum."""
    lines = [
        f"| Pairs | {_HEAD} |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        _row("Every pair of in-domain strings", scores.all_pairs),
        _row("Drawn pairs", scores.drawn),
    ]
    return "\n".join(lines) + "\n\n" + strata_table(scores) + "\n" + footer(scores)


# Replaying the recorded proposals

README_BLOCK = "normalization-eval"


def labeled(out_dir: Path) -> list[KindLabel]:
    """The outcome kinds, every string labeled and checked against the draw."""
    report = outcome_kinds.check(out_dir)
    if report.problems:
        raise ValueError("; ".join(report.problems))
    return report.labels


def replayed(
    labels: Sequence[KindLabel],
    config: QuestionConfig,
    replay_dir: Path,
    choice: ModelChoice,
    drawn: PairSample,
    baseline: bool,
) -> Proposal:
    """The current prompt's recorded proposal, or with baseline the one the pairs were drawn
    from, replayed by its key under any later prompt."""
    strings = sorted(label.string for label in labels)
    params = None
    if baseline:
        try:
            params = recorded_request(replay_dir, drawn.proposal)
        except FileNotFoundError as error:
            raise ValueError(f"no recorded proposal in {replay_dir}") from error
        if params["messages"][0]["content"] != normalize.user_message(strings):
            raise ValueError(
                "the kind file's strings are not the ones the drawn proposal was made on"
            )
    client = normalize.model_client(replay_dir, False)
    try:
        proposal = normalize.propose(strings, config, client, choice, params=params)
    except ReplayMiss as error:
        raise ValueError(f"no recorded proposal in {replay_dir}") from error
    if proposal.outcomes is None:
        raise ValueError(f"the recorded proposal is invalid: {'; '.join(proposal.errors)}")
    return proposal


def readme(out_dir: Path, replay_dir: Path, config: QuestionConfig, choice: ModelChoice) -> str:
    """The README block: each proposal before and after the corrections its disagreements found,
    then the frozen prompt's proposal by stratum."""
    labels = labeled(out_dir)
    drawn = load_sample(out_dir / SAMPLE_FILE)
    corrections = load_corrections(out_dir / CORRECTIONS_FILE)
    proposals = [
        (
            "First proposal",
            replayed(labels, config, replay_dir, choice, drawn, True),
            Found.FIRST_PROPOSAL,
        ),
        (
            "Rules prompt, frozen",
            replayed(labels, config, replay_dir, choice, drawn, False),
            Found.RULES_PROPOSAL,
        ),
    ]
    head = ["Proposal", "Labels", "Scored pairs", "Merge precision", "Merge recall"]
    head += ["Drawn precision", "Drawn recall"]
    rows = ["| " + " | ".join(head) + " |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    final: NormalizationScores | None = None
    for name, proposal, found in proposals:
        assert proposal.outcomes is not None
        count = sum(c.found is found for c in corrections)
        versions = [
            (
                f"Before the {count} corrections its disagreements found",
                before(labels, corrections, {found}),
            ),
            (f"All {len(corrections)} corrections", labels),
        ]
        for described, version in versions:
            s = score(version, proposal.outcomes, proposal.outside, drawn)
            cells = [name, described, f"{s.all_pairs.pairs:,}"]
            cells += [_rate(s.all_pairs.precision), _rate(s.all_pairs.recall)]
            cells += [_rate(s.drawn.precision), _rate(s.drawn.recall)]
            rows.append("| " + " | ".join(cells) + " |")
            final = s
    assert final is not None
    return (
        f"Outcome proposals by {proposals[1][1].model} on the {final.strings} outcome strings, "
        "scored from the committed recordings over every pair of in-domain strings and over the "
        f"{final.drawn.pairs} drawn pairs that are in the domain. In sample: the outcome rules "
        "were written while labeling these same strings, so the rules prompt's rates are not "
        "held-out estimates. A correction found only where a proposal disagreed can raise its "
        "score but never lower it, so each proposal is shown before and after those.\n\n"
        + "\n".join(rows)
        + "\n\nThe frozen prompt's proposal, against all corrections, by stratum of the draw; "
        "the strata come from the first proposal.\n\n" + strata_table(final) + "\n" + footer(final)
    )
