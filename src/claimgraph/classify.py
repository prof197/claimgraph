"""Classifying a candidate pair: is the disagreement genuine or apparent, and why?

The classifier sees both headline findings' fields, never the abstracts, and judges by the
labeling guide's own rules, so it and the labeler work from the same text. The single arms send
one request per pair to one model.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from claimgraph.compare import direction
from claimgraph.config import QuestionConfig
from claimgraph.extract import SETTINGS, ModelChoice, ModelSettings, strip_schema
from claimgraph.kind_review import guide_section
from claimgraph.llm import ModelClient, Reply, Usage, request_key, retry_request
from claimgraph.models import (
    Arm,
    Design,
    Finding,
    NonBlank,
    PairClassification,
    Reason,
    Record,
    Verdict,
)
from claimgraph.normalize import OutcomeMapping
from claimgraph.pair_sample import DrawnPair
from claimgraph.prices import Prices, cost, model_prices

GUIDE_SECTION = "Contradiction pairs"
# A classification is a few short fields; a response cut off here is invalid and retried.
MAX_TOKENS = 2048
# The single arms, by the model each runs on.
SINGLE_ARMS: dict[Arm, ModelChoice] = {
    Arm.SINGLE_SMALL: ModelChoice.HAIKU,
    Arm.SINGLE_MID: ModelChoice.SONNET,
}
_DESIGN_NAMES = {
    Design.RCT: "randomized controlled trial",
    Design.NON_RANDOMIZED_TRIAL: "non-randomized controlled trial",
    Design.COHORT: "cohort study",
    Design.CASE_CONTROL: "case-control study",
    Design.CROSS_SECTIONAL: "cross-sectional study",
    Design.CASE_SERIES: "case series",
    Design.OTHER: "other primary study",
}
_DIRECTION_NAMES = {"benefit": "benefit", "harm": "harm", "none": "no effect"}


class ClassifierOutput(Record):
    """What the model returns for one pair. The rationale comes first, so the judgment follows
    from it."""

    rationale: NonBlank = Field(
        description="One short sentence explaining the judgment, naming the deciding difference."
    )
    verdict: Verdict = Field(description="genuine or apparent, by the rules.")
    reason: Reason = Field(description="The primary reason, by the rules.")
    also_differs: list[Reason] = Field(
        description="Other differences present; never the primary reason again, never unclear."
    )
    detail: NonBlank = Field(
        description=(
            "A short specific, such as 'duration: 8 weeks vs a median 5.3 years'; for an "
            "apparent pair, what dissolves the conflict, such as 'underpowered: n = 40'."
        )
    )


class PairSide(Record):
    """One study's side of a pair, as the classifier sees it."""

    design: Design
    sample_size: int | None
    finding: Finding


class PairInput(Record):
    pair: DrawnPair
    higher_is_better: bool
    a: PairSide
    b: PairSide


class ClassifyResult(Record):
    pair_id: str
    arm: Arm
    model: str
    # None when both attempts were invalid; the run counts these.
    classification: PairClassification | None
    errors: list[str]
    usage: list[Usage]


class InvalidOutput(ValueError):
    pass


def settings(arm: Arm) -> ModelSettings:
    return SETTINGS[SINGLE_ARMS[arm]]


def pair_inputs(
    pairs: Sequence[DrawnPair],
    findings: Mapping[str, Finding],
    studies: Mapping[str, tuple[Design, int | None]],
    mapping: OutcomeMapping,
) -> list[PairInput]:
    """Each pair with both headline findings; `studies` maps a PMID to its design and size."""
    polarity = {o.name: o.higher_is_better for o in mapping.outcomes}

    def side(finding_id: str) -> PairSide:
        finding = findings[finding_id]
        design, size = studies[finding.pmid]
        return PairSide(design=design, sample_size=size, finding=finding)

    return [
        PairInput(
            pair=p,
            higher_is_better=polarity[p.outcome],
            a=side(p.finding_a),
            b=side(p.finding_b),
        )
        for p in pairs
    ]


def _dimensions(config: QuestionConfig) -> str:
    if not config.dimensions:
        return "None for this question."
    lines: list[str] = []
    for d in config.dimensions:
        options = f" Values: {', '.join(d.options)}." if d.options else ""
        unit = f" Unit: {d.unit}." if d.unit else ""
        lines.append(f"- {d.name} refines {d.refines.value}.{options}{unit}")
    return "\n".join(lines)


def _rules(guide: str) -> str:
    """The guide's rules for a pair, from the verdict on: the file format before them is the
    labeler's, not the classifier's."""
    section = guide_section(guide, GUIDE_SECTION)
    return section[section.index("**`verdict`**") :]


def system_prompt(config: QuestionConfig, guide: str) -> str:
    return f"""\
You judge a pair of studies whose findings on one outcome point different ways, for a map of \
the evidence on one question.

Question: {config.title}

Each study is shown by its headline finding on the outcome: the claim, the sentence it was \
taken from, what was measured, who was enrolled, what they got and what it was compared with, \
the design and the sample size. Judge from these fields alone, not from your own knowledge of \
the studies. Decide whether the disagreement is genuine or apparent, and name the reason, by \
the rules below. Return one JSON object that follows the schema.

## Rules

{_rules(guide)}

## Dimensions

Custom fields the question adds to each finding. Each refines one reason and never adds one.

{_dimensions(config)}
"""


def _side(label: str, side: PairSide, higher_is_better: bool) -> str:
    f = side.finding
    found = _DIRECTION_NAMES[direction(f.effect, higher_is_better).value]
    size = "not reported" if side.sample_size is None else f"{side.sample_size:,}"
    lines = [
        f"## Study {label}",
        f"Found: {found}",
        f"Claim: {f.claim}",
        f'Sentence: "{f.quote}"',
        f"Measured as: {f.outcome_reported}",
        f"Population: {f.population}",
        f"Intervention: {f.intervention}",
        f"Comparator: {f.comparator}",
        f"Design: {_DESIGN_NAMES.get(side.design, side.design.value)}",
        f"Sample size: {size}",
    ]
    lines += [f"{name}: {value}" for name, value in sorted(f.dimensions.items())]
    return "\n".join(lines)


def user_message(item: PairInput) -> str:
    better = "higher" if item.higher_is_better else "lower"
    return "\n\n".join(
        [
            f"Outcome: {item.pair.outcome} ({better} is better)",
            _side("A", item.a, item.higher_is_better),
            _side("B", item.b, item.higher_is_better),
        ]
    )


def response_schema() -> dict[str, Any]:
    schema: dict[str, Any] = strip_schema(ClassifierOutput.model_json_schema())
    return schema


def request(item: PairInput, config: QuestionConfig, guide: str, arm: Arm) -> dict[str, Any]:
    s = settings(arm)
    return {
        "model": s.model,
        "max_tokens": MAX_TOKENS,
        # The same for every pair: the cached prefix.
        "system": [
            {
                "type": "text",
                "text": system_prompt(config, guide),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_message(item)}],
        "output_config": {
            "effort": s.effort,
            "format": {"type": "json_schema", "schema": response_schema()},
        },
        "thinking": s.thinking,
    }


def read_classification(reply: Reply, pair_id: str) -> PairClassification:
    if reply.stop_reason == "max_tokens":
        raise InvalidOutput("the output was cut off")
    try:
        out = ClassifierOutput.model_validate_json(reply.text)
        return PairClassification(pair_id=pair_id, **out.model_dump())
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "output"
        raise InvalidOutput(f"{where}: {first['msg']}") from error


def _note(error: str) -> str:
    return (
        f"That output was invalid: {error}. Return the complete JSON object again, corrected, "
        "following the same rules."
    )


def classify_pair(
    item: PairInput, config: QuestionConfig, guide: str, client: ModelClient, arm: Arm
) -> ClassifyResult:
    """One pair's classification; invalid output gets one retry naming the error."""
    params = request(item, config, guide, arm)
    errors: list[str] = []
    usage: list[Usage] = []
    classification: PairClassification | None = None
    for attempt in range(2):
        reply = client.create(params)
        usage.append(reply.usage)
        try:
            classification = read_classification(reply, item.pair.pair_id)
            break
        except InvalidOutput as error:
            errors.append(str(error))
            if attempt == 0:
                params = retry_request(params, reply, _note(str(error)))
    return ClassifyResult(
        pair_id=item.pair.pair_id,
        arm=arm,
        model=settings(arm).model,
        classification=classification,
        errors=errors,
        usage=usage,
    )


# The dry run

# Characters per token, set low on purpose, as for extraction.
CHARS_PER_TOKEN = 3.0
# Output tokens for a typical classification, before any is measured: the few short fields of
# the schema. The dev run replaces it with a measured mean.
TYPICAL_OUTPUT_TOKENS = 250


class Estimate(Record):
    arm: Arm
    model: str
    # Pairs whose first request has no recording yet.
    pairs: int
    input_tokens: int
    # One request each with typical output and no cache reads, at batch prices.
    typical_usd: float
    # Every pair taking both requests, each with max_tokens of output and no cache reads, at
    # batch prices. Real spend cannot exceed it.
    ceiling_usd: float


def estimate(
    items: Sequence[PairInput],
    config: QuestionConfig,
    guide: str,
    arm: Arm,
    replay_dir: Path,
    prices: Prices,
) -> Estimate:
    """What sending the unrecorded first requests would cost, before anything is sent."""
    model = settings(arm).model
    p = model_prices(model, prices)
    rate = prices.batch_discount / 1_000_000
    left = input_tokens = 0
    typical = ceiling = 0.0
    for item in items:
        params = request(item, config, guide, arm)
        if (replay_dir / f"{request_key(params)}.json").exists():
            continue
        sent = {k: params[k] for k in ("system", "messages", "output_config")}
        tokens = round(len(json.dumps(sent, ensure_ascii=False)) / CHARS_PER_TOKEN)
        out = MAX_TOKENS
        left += 1
        input_tokens += tokens
        typical += (tokens * p.input + TYPICAL_OUTPUT_TOKENS * p.output) * rate
        retry = tokens + out
        ceiling += ((tokens + retry) * p.input + 2 * out * p.output) * rate
    return Estimate(
        arm=arm,
        model=model,
        pairs=left,
        input_tokens=input_tokens,
        typical_usd=typical,
        ceiling_usd=ceiling,
    )


def summary_lines(results: Sequence[ClassifyResult], prices: Prices) -> list[str]:
    """Counts, verdicts, reasons and measured spend at batch prices, for the terminal."""
    done = [r.classification for r in results if r.classification is not None]
    verdicts = {v: sum(c.verdict is v for c in done) for v in Verdict}
    reasons = {r: sum(c.reason is r for c in done) for r in Reason}
    usd = sum(cost(u, r.model, prices, batch=True) for r in results for u in r.usage)
    outputs = [u.output_tokens for r in results for u in r.usage]
    mean_out = sum(outputs) / len(outputs) if outputs else 0
    retried = sum(len(r.usage) > 1 for r in results)
    return [
        f"{len(done)} of {len(results)} pairs classified, {retried} retried, "
        f"{len(results) - len(done)} invalid twice",
        "verdicts: " + ", ".join(f"{v.value} {n}" for v, n in verdicts.items()),
        "reasons: " + ", ".join(f"{r.value} {n}" for r, n in reasons.items() if n),
        f"measured: ${usd:.4f} at batch prices, {mean_out:.0f} output tokens per request",
    ]


def describe_estimate(result: Estimate) -> str:
    return (
        f"{result.arm.value} on {result.model}: {result.pairs} unrecorded pairs, about "
        f"{result.input_tokens:,} input tokens; typical ${result.typical_usd:.4f}, "
        f"at most ${result.ceiling_usd:.4f} at batch prices. Nothing was sent."
    )
