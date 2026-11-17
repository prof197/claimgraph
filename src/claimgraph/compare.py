"""Candidate contradiction pairs, from the extracted fields alone; no model is involved.

A pair is two studies on one normalized outcome whose headline findings point different ways:
an effect against no effect, or benefit against harm. Papers from one trial are one study, so a
trial never pairs with itself and enters each pair once.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from enum import StrEnum
from itertools import combinations

from pydantic import Field

from claimgraph.extract import ExtractionResult
from claimgraph.models import (
    DEFAULT_EXCLUDED_DESIGNS,
    REVIEW_DESIGNS,
    CandidatePair,
    Design,
    Effect,
    Finding,
    PairKind,
    Record,
)
from claimgraph.normalize import ExclusionReason, OutcomeMapping


class Direction(StrEnum):
    BENEFIT = "benefit"
    HARM = "harm"
    NONE = "none"


def direction(effect: Effect, higher_is_better: bool) -> Direction:
    """Benefit or harm: the effect with more of the exposure, read by the string's polarity."""
    if effect is Effect.NO_CHANGE:
        return Direction.NONE
    better = (effect is Effect.INCREASED) == higher_is_better
    return Direction.BENEFIT if better else Direction.HARM


def pair_kind(a: Direction, b: Direction) -> PairKind | None:
    if a == b:
        return None
    if Direction.NONE in (a, b):
        return PairKind.EFFECT_VS_NONE
    return PairKind.BENEFIT_VS_HARM


def study_keys(trial_ids: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Each paper's study key, by PMID.

    Papers sharing a trial ID are one study, transitively: the connected components of papers
    joined by shared IDs. A study with registrations is keyed by the first of them in sorted
    order, a paper with none by its PMID.
    """
    parent = {pmid: pmid for pmid in trial_ids}

    def root(pmid: str) -> str:
        while parent[pmid] != pmid:
            parent[pmid] = parent[parent[pmid]]
            pmid = parent[pmid]
        return pmid

    first_paper: dict[str, str] = {}
    for pmid, ids in trial_ids.items():
        for trial in ids:
            if trial in first_paper:
                parent[root(pmid)] = root(first_paper[trial])
            else:
                first_paper[trial] = pmid
    components: dict[str, list[str]] = defaultdict(list)
    for pmid in trial_ids:
        components[root(pmid)].append(pmid)
    keys: dict[str, str] = {}
    for papers in components.values():
        registered = sorted({trial for pmid in papers for trial in trial_ids[pmid]})
        key = registered[0] if registered else papers[0]
        keys |= {pmid: key for pmid in papers}
    return keys


def headline(findings: Sequence[Finding]) -> Finding | None:
    """A study's headline finding on one outcome, from its findings there in reported order.

    Its primary finding, else its first that is not a subgroup result; None when every one is a
    subgroup result, since a subgroup cannot speak for the whole sample. The summary strip
    counts the same finding.
    """
    for finding in findings:
        if finding.is_primary and not finding.is_subgroup:
            return finding
    return next((f for f in findings if not f.is_subgroup), None)


class Headline(Record):
    outcome: str
    study: str
    pmid: str
    finding_id: str
    direction: Direction


class Block(Record):
    """One outcome's share of the comparison: the classification cost grows with its pairs."""

    outcome: str
    studies: int
    # Studies whose only findings on the outcome are subgroup results: shown, never paired.
    subgroup_only: int
    pairs: int


class Candidates(Record):
    pairs: list[CandidatePair]
    blocks: list[Block]
    headlines: list[Headline]
    # Findings on strings the mapping drops as out of domain.
    out_of_domain: int
    # Findings the review excluded one by one, by reason.
    excluded_findings: dict[ExclusionReason, int] = Field(
        default_factory=dict[ExclusionReason, int]
    )


def candidates(
    results: Sequence[ExtractionResult],
    trial_ids: Mapping[str, Sequence[str]],
    mapping: OutcomeMapping,
    exclude_designs: frozenset[Design] = DEFAULT_EXCLUDED_DESIGNS,
) -> Candidates:
    """Every candidate pair, outcome by outcome. Every outcome string must be in the mapping.

    Reviews and the question's excluded designs are left out: they are not counted, so they are
    not compared either.
    """
    outcome_of = {m: o for o in mapping.outcomes for m in o.members}
    dropped = set(mapping.out_of_domain)
    excluded = mapping.excluded()
    excluded_findings: dict[ExclusionReason, int] = defaultdict(int)
    keys = study_keys(trial_ids)
    found: dict[tuple[str, str], list[tuple[str, Finding]]] = defaultdict(list)
    out_of_domain = 0
    # Earlier papers first, so a trial's first-reported finding comes from its earliest paper.
    for result in sorted(results, key=lambda r: int(r.pmid)):
        study = result.study
        if study is None or study.design in REVIEW_DESIGNS | exclude_designs:
            continue
        for finding in study.findings:
            if finding.outcome_reported in dropped:
                out_of_domain += 1
                continue
            if finding.finding_id in excluded:
                excluded_findings[excluded[finding.finding_id]] += 1
                continue
            outcome = outcome_of.get(finding.outcome_reported)
            if outcome is None:
                raise ValueError(f"{finding.outcome_reported!r} is not in the mapping")
            key = keys.get(result.pmid, result.pmid)
            found[(outcome.name, key)].append((result.pmid, finding))
    polarity = mapping.polarity()
    headlines: list[Headline] = []
    subgroup_only: dict[str, int] = defaultdict(int)
    for (outcome, key), papers in sorted(found.items()):
        lead = headline([f for _, f in papers])
        if lead is None:
            subgroup_only[outcome] += 1
            continue
        pmid = next(p for p, f in papers if f is lead)
        headlines.append(
            Headline(
                outcome=outcome,
                study=key,
                pmid=pmid,
                finding_id=lead.finding_id,
                direction=direction(lead.effect, polarity[lead.outcome_reported]),
            )
        )
    pairs: list[CandidatePair] = []
    blocks: list[Block] = []
    for outcome in sorted({o for o, _ in found}):
        leads = [h for h in headlines if h.outcome == outcome]
        made = [
            CandidatePair(outcome=outcome, study_a=a.study, study_b=b.study, kind=kind)
            for a, b in combinations(leads, 2)
            if (kind := pair_kind(a.direction, b.direction)) is not None
        ]
        pairs += made
        blocks.append(
            Block(
                outcome=outcome,
                studies=len(leads),
                subgroup_only=subgroup_only[outcome],
                pairs=len(made),
            )
        )
    blocks.sort(key=lambda b: (-b.pairs, b.outcome))
    return Candidates(
        pairs=pairs,
        blocks=blocks,
        headlines=headlines,
        out_of_domain=out_of_domain,
        excluded_findings=dict(sorted(excluded_findings.items())),
    )
