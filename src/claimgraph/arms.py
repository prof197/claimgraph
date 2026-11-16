"""The multi-call classification arms: a council of vendors, with and without peer ranking, and
one model sampled several times, voted or synthesized.

Every arm asks the same question the single arms ask, with the frozen classification prompt.
The council's members are one mid-tier model from each of three vendors; Sonnet 5.5, the
single-mid model, chairs. Ranking follows llm-council: each member ranks the anonymized answers
before the chair sees them. Each call gets one retry on invalid output, as a single arm's does,
and a step's calls go out together, so one batch round carries all of them.
"""

import json
import random
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from claimgraph import classify
from claimgraph.classify import (
    MAX_TOKENS,
    Call,
    ClassifierOutput,
    ClassifyResult,
    InvalidOutput,
    PairInput,
)
from claimgraph.config import QuestionConfig
from claimgraph.extract import SETTINGS, ModelChoice, ModelSettings, strip_schema
from claimgraph.llm import (
    ModelClient,
    ReplayMiss,
    Reply,
    Usage,
    Vendor,
    gather,
    retry_request,
    vendor_of,
    wrap,
)
from claimgraph.models import Arm, NonBlank, PairClassification, Reason, Record, Verdict
from claimgraph.prices import Prices, model_prices


class Member(Record):
    """One model the multi-call arms ask; vendor None is Claude."""

    vendor: Vendor | None
    model: str
    # The vendor's lowest reasoning setting; None for Claude, which runs without thinking.
    reasoning: str | None


SONNET = Member(vendor=None, model=SETTINGS[ModelChoice.SONNET].model, reasoning=None)
# One mid-tier model from each of three vendors, priced alike: $2 / $10 per million tokens at
# standard rates when chosen. Neither other model can turn reasoning off; "low" is the least.
COUNCIL = (
    SONNET,
    Member(vendor=Vendor.OPENAI, model="gpt-6.1-sol", reasoning="low"),
    Member(vendor=Vendor.GOOGLE, model="gemini-4-argon", reasoning="low"),
)
CHAIR = SONNET
# Self-MoA proposes as many answers as the council does, from one model.
SELF_MOA_SAMPLES = len(COUNCIL)
# Matched to council-ranked's billed tokens per pair, hidden reasoning included, as measured
# on the eval pairs; None until that run is made.
SELF_CONSISTENCY_K: int | None = None
type Letter = Literal["A", "B", "C", "D", "E", "F", "G", "H"]
LETTERS: tuple[Letter, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")
MULTI_ARMS = (Arm.COUNCIL, Arm.COUNCIL_RANKED, Arm.SELF_CONSISTENCY, Arm.SELF_MOA)
# The deepest chain of requests a pair takes: each step and its retry is a batch round.
ROUNDS: dict[Arm, int] = {
    Arm.COUNCIL: 4,
    Arm.COUNCIL_RANKED: 6,
    Arm.SELF_CONSISTENCY: 2,
    Arm.SELF_MOA: 4,
}


class Ranking(Record):
    """A member's review of the anonymized answers, best first."""

    evaluation: NonBlank = Field(
        description="A few sentences weighing each response against the rules and the fields."
    )
    ranking: list[Letter] = Field(description="Every response's letter once, best first.")


def vendors(arm: Arm) -> set[Vendor]:
    """The vendors other than Anthropic an arm sends to."""
    if arm in (Arm.COUNCIL, Arm.COUNCIL_RANKED):
        return {m.vendor for m in COUNCIL if m.vendor is not None}
    return set()


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema with every local $ref replaced by its definition, for vendors whose JSON
    Schema support is narrower than Claude's."""
    defs: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(n) for n in cast(list[Any], node)]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in cast(dict[str, Any], node).items():
            if key == "$defs":
                continue
            if key == "$ref":
                out |= resolve(defs[str(value).removeprefix("#/$defs/")])
            else:
                out[key] = resolve(value)
        return out

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def openapi_schema(node: Any) -> Any:
    """A JSON Schema in the OpenAPI form Gemini's response_schema takes: no
    additionalProperties, and a member that may be null marked nullable. Gemini's batches
    ignore a response_json_schema and enforce this one."""
    if isinstance(node, list):
        return [openapi_schema(n) for n in cast(list[Any], node)]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in cast(dict[str, Any], node).items():
        if key == "additionalProperties":
            continue
        if key == "anyOf":
            members = cast(list[Any], value)
            rest = [m for m in members if m != {"type": "null"}]
            if len(rest) == 1 and len(rest) < len(members):
                out |= openapi_schema(rest[0])
                out["nullable"] = True
                continue
        out[key] = openapi_schema(value)
    return out


def member_request(
    member: Member, system: str, user: str, schema: dict[str, Any], name: str
) -> dict[str, Any]:
    """One member's request in its vendor's own format."""
    if member.vendor is None:
        settings = SETTINGS[ModelChoice.SONNET]
        return classify.claude_request(
            ModelSettings(model=member.model, effort=settings.effort, thinking=settings.thinking),
            system,
            user,
            schema,
        )
    flat = inline_refs(schema)
    if member.vendor is Vendor.OPENAI:
        return wrap(
            member.vendor,
            {
                "model": member.model,
                "instructions": system,
                "input": [{"role": "user", "content": user}],
                "text": {
                    "format": {"type": "json_schema", "name": name, "schema": flat, "strict": True}
                },
                "reasoning": {"effort": member.reasoning},
                # Output includes the hidden reasoning, so this caps both.
                "max_output_tokens": MAX_TOKENS,
                "store": False,
            },
        )
    return wrap(
        member.vendor,
        {
            "model": member.model,
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "config": {
                "system_instruction": system,
                "response_mime_type": "application/json",
                "response_schema": openapi_schema(flat),
                "thinking_config": {"thinking_level": member.reasoning},
                "max_output_tokens": MAX_TOKENS,
            },
        },
    )


def model_of(params: dict[str, Any]) -> str:
    return params["body"]["model"] if vendor_of(params) is not None else params["model"]


@dataclass
class Answer[T]:
    value: T | None
    errors: list[str] = field(default_factory=list[str])
    calls: list[Call] = field(default_factory=list[Call])


def ask_all[T](
    client: ModelClient, requests: Sequence[dict[str, Any]], read: Callable[[Reply], T]
) -> list[Answer[T]]:
    """Every request's answer, each with one retry on invalid output. The first attempts go out
    together, then the retries together, so a missing step costs one batch round, not one per
    request."""
    answers: list[Answer[T]] = [Answer(None) for _ in requests]
    retries: dict[int, dict[str, Any]] = {}
    replies = _create_all(client, requests)
    for n, (params, reply) in enumerate(zip(requests, replies, strict=True)):
        answers[n].calls.append(Call(model=model_of(params), usage=reply.usage))
        try:
            answers[n].value = read(reply)
        except InvalidOutput as error:
            answers[n].errors.append(str(error))
            retries[n] = retry_request(params, reply, classify.note(str(error)))
    second = _create_all(client, list(retries.values()))
    for (n, params), reply in zip(retries.items(), second, strict=True):
        answers[n].calls.append(Call(model=model_of(params), usage=reply.usage))
        try:
            answers[n].value = read(reply)
        except InvalidOutput as error:
            answers[n].errors.append(str(error))
    return answers


def _create_all(client: ModelClient, requests: Sequence[dict[str, Any]]) -> list[Reply]:
    replies: list[Reply] = []
    misses: list[ReplayMiss] = []
    for params in requests:
        try:
            replies.append(client.create(params))
        except ReplayMiss as miss:
            misses.append(miss)
    if misses:
        raise gather(misses)
    return replies


def _output(reply: Reply) -> ClassifierOutput:
    return classify.read_model(reply, ClassifierOutput)


def _labeled[T](values: Sequence[T], pair_id: str, step: str) -> list[tuple[Letter, T]]:
    """The values under letters in an order fixed by the pair, so no model is known by its place."""
    shuffled = list(values)
    random.Random(f"{pair_id}|{step}").shuffle(shuffled)
    return list(zip(LETTERS, shuffled, strict=False))


def _answers_text(labeled: Sequence[tuple[Letter, ClassifierOutput]]) -> str:
    blocks = [
        f"### Response {letter}\n```json\n{out.model_dump_json(indent=1)}\n```"
        for letter, out in labeled
    ]
    return "\n\n".join(blocks)


_CHAIR_ROLE = """

## Your role

You chair a council. {source} answered the pair below independently, under the same rules; \
their answers follow it, as anonymous responses. Weigh each against the rules and the studies' \
fields, not by counting votes, and return the one classification the rules support. You may \
agree with any response, combine them, or depart from all of them.
"""
_RANKER_ROLE = """

## Your role

You review anonymous answers to the pair below, each from a different model, under the same \
rules. Weigh each response against the rules and the studies' fields, then rank every response \
once, best first.
"""


def _chair_request(
    item: PairInput,
    config: QuestionConfig,
    guide: str,
    source: str,
    labeled: Sequence[tuple[Letter, ClassifierOutput]],
    rankings: str = "",
) -> dict[str, Any]:
    system = classify.system_prompt(config, guide) + _CHAIR_ROLE.format(source=source)
    user = f"{classify.user_message(item)}\n\n## Responses\n\n{_answers_text(labeled)}{rankings}"
    return member_request(CHAIR, system, user, classify.response_schema(), "classification")


def _ranker_request(
    member: Member,
    item: PairInput,
    config: QuestionConfig,
    guide: str,
    labeled: Sequence[tuple[Letter, ClassifierOutput]],
) -> dict[str, Any]:
    system = classify.system_prompt(config, guide) + _RANKER_ROLE
    user = f"{classify.user_message(item)}\n\n## Responses\n\n{_answers_text(labeled)}"
    schema: dict[str, Any] = strip_schema(Ranking.model_json_schema())
    return member_request(member, system, user, schema, "ranking")


def _read_ranking(letters: Sequence[Letter]) -> Callable[[Reply], Ranking]:
    def read(reply: Reply) -> Ranking:
        ranking = classify.read_model(reply, Ranking)
        if sorted(ranking.ranking) != sorted(letters):
            raise InvalidOutput(f"ranking: rank each of {', '.join(letters)} exactly once")
        return ranking

    return read


def _mean_positions(rankings: Sequence[Ranking], letters: Sequence[Letter]) -> str:
    """The peers' rankings as each response's mean position, best first."""
    means = {
        letter: sum(r.ranking.index(letter) + 1 for r in rankings) / len(rankings)
        for letter in letters
    }
    rows = [
        f"- Response {letter}: {mean:.2f}"
        for letter, mean in sorted(means.items(), key=lambda kv: kv[1])
    ]
    return (
        f"\n\n## Peer rankings\n\n{len(rankings)} council members ranked the responses; each "
        "response's mean position, where 1 is best:\n\n" + "\n".join(rows)
    )


def _result(
    item: PairInput, arm: Arm, final: ClassifierOutput | None, answers: Sequence[Answer[Any]]
) -> ClassifyResult:
    return ClassifyResult(
        pair_id=item.pair.pair_id,
        arm=arm,
        classification=(
            None
            if final is None
            else PairClassification(pair_id=item.pair.pair_id, **final.model_dump())
        ),
        errors=[e for a in answers for e in a.errors],
        calls=[c for a in answers for c in a.calls],
    )


def council(
    item: PairInput, config: QuestionConfig, guide: str, client: ModelClient, ranked: bool
) -> ClassifyResult:
    arm = Arm.COUNCIL_RANKED if ranked else Arm.COUNCIL
    system = classify.system_prompt(config, guide)
    user = classify.user_message(item)
    schema = classify.response_schema()
    first = [member_request(m, system, user, schema, "classification") for m in COUNCIL]
    proposals = ask_all(client, first, _output)
    answered = [(m, a.value) for m, a in zip(COUNCIL, proposals, strict=True) if a.value]
    spent: list[Answer[Any]] = list(proposals)
    if not answered:
        return _result(item, arm, None, spent)
    labeled = _labeled([out for _, out in answered], item.pair.pair_id, "proposals")
    rankings = ""
    if ranked:
        letters: list[Letter] = [letter for letter, _ in labeled]
        reviews = ask_all(
            client,
            [_ranker_request(m, item, config, guide, labeled) for m, _ in answered],
            _read_ranking(letters),
        )
        spent += reviews
        done = [r.value for r in reviews if r.value is not None]
        if done:
            rankings = _mean_positions(done, letters)
    source = f"{len(answered)} models, each from a different vendor,"
    (chair,) = ask_all(
        client, [_chair_request(item, config, guide, source, labeled, rankings)], _output
    )
    return _result(item, arm, chair.value, [*spent, chair])


def _sample_request(
    item: PairInput, config: QuestionConfig, guide: str, tag: str
) -> dict[str, Any]:
    """The single-mid request, marked so each sample is its own recording. Current models take
    no sampling parameters, so samples differ as repeated calls do; the mark is metadata the
    model never sees."""
    return classify.request(item, config, guide, Arm.SINGLE_MID) | {"metadata": {"user_id": tag}}


def vote(outputs: Sequence[ClassifierOutput]) -> ClassifierOutput:
    """The most common verdict, then the most common reason among the answers with it; ties go
    to the earlier sample. The earliest answer with the winning pair is returned whole."""
    verdicts = Counter(o.verdict for o in outputs)
    top = max(verdicts.values())
    verdict = next(o.verdict for o in outputs if verdicts[o.verdict] == top)
    reasons = Counter(o.reason for o in outputs if o.verdict is verdict)
    best = max(reasons.values())
    return next(o for o in outputs if o.verdict is verdict and reasons[o.reason] == best)


def self_consistency(
    item: PairInput, config: QuestionConfig, guide: str, client: ModelClient, k: int
) -> ClassifyResult:
    requests = [_sample_request(item, config, guide, f"self-consistency-{n}") for n in range(k)]
    samples = ask_all(client, requests, _output)
    outputs = [s.value for s in samples if s.value is not None]
    return _result(item, Arm.SELF_CONSISTENCY, vote(outputs) if outputs else None, samples)


def self_moa(
    item: PairInput, config: QuestionConfig, guide: str, client: ModelClient
) -> ClassifyResult:
    requests = [
        _sample_request(item, config, guide, f"self-moa-{n}") for n in range(SELF_MOA_SAMPLES)
    ]
    samples = ask_all(client, requests, _output)
    outputs = [s.value for s in samples if s.value is not None]
    if not outputs:
        return _result(item, Arm.SELF_MOA, None, samples)
    labeled = _labeled(outputs, item.pair.pair_id, "samples")
    source = f"{len(outputs)} samples of one model"
    (chair,) = ask_all(client, [_chair_request(item, config, guide, source, labeled)], _output)
    return _result(item, Arm.SELF_MOA, chair.value, [*samples, chair])


def attempt(
    arm: Arm, config: QuestionConfig, guide: str, k: int | None
) -> Callable[[PairInput, ModelClient], ClassifyResult]:
    """One pair's classification under `arm`, for a batch run."""
    if arm in classify.SINGLE_ARMS:
        return lambda item, client: classify.classify_pair(item, config, guide, client, arm)
    if arm is Arm.COUNCIL or arm is Arm.COUNCIL_RANKED:
        ranked = arm is Arm.COUNCIL_RANKED
        return lambda item, client: council(item, config, guide, client, ranked)
    if arm is Arm.SELF_MOA:
        return lambda item, client: self_moa(item, config, guide, client)
    if k is None:
        raise ValueError("self-consistency needs k, matched to council-ranked's billed tokens")
    return lambda item, client: self_consistency(item, config, guide, client, k)


def matched_k(
    council_ranked: Sequence[ClassifyResult], single_mid: Sequence[ClassifyResult]
) -> int:
    """Self-consistency's k: council-ranked's billed tokens per pair over one single-mid
    sample's, rounded, so the two arms spend the same tokens."""
    sample = classify.billed_per_pair(single_mid)
    if not sample:
        raise ValueError("no single-mid tokens to match against")
    return max(1, round(classify.billed_per_pair(council_ranked) / sample))


# The dry run

# A typical answer's visible output, from the dev runs: Haiku 192 and Sonnet 164 tokens.
TYPICAL_VISIBLE_TOKENS = 190
# Hidden reasoning per call at the other vendors' lowest setting, before any is measured: an
# assumption, set high, that the live run replaces with its counts.
TYPICAL_REASONING_TOKENS = 1000
_PLACEHOLDER = ClassifierOutput(
    rationale=(
        "Placeholder rationale of typical length for the dry run: the deciding difference "
        "between the two studies, named in one sentence of about forty words, as a real "
        "answer names it."
    ),
    verdict=Verdict.GENUINE,
    reason=Reason.POPULATION,
    also_differs=[Reason.DESIGN_OR_BIAS],
    detail="placeholder: a short specific of typical length",
)


class Simulated(ModelClient):
    """A client that sends nothing and answers every request with a typical placeholder, so the
    dry run sees the exact requests each step of an arm would make."""

    def __init__(self) -> None:
        super().__init__(Path())
        self.sent: list[dict[str, Any]] = []

    def create(self, params: Mapping[str, Any]) -> Reply:
        self.sent.append(dict(params))
        text = json.dumps(params, ensure_ascii=False)
        if '"ranking"' in text:
            shown = sorted(set(re.findall(r"### Response ([A-H])", text)))
            answer = Ranking(
                evaluation="Placeholder evaluation.", ranking=cast(list[Letter], shown)
            )
            body = answer.model_dump_json()
        else:
            body = _PLACEHOLDER.model_dump_json()
        empty = Usage(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return Reply(
            id="simulated", model=model_of(dict(params)), stop_reason=None, text=body, usage=empty
        )


class ArmEstimate(Record):
    arm: Arm
    # Pairs the arm has not finished from the recordings.
    pairs: int
    requests: int
    # Typical visible output on every request, plus TYPICAL_REASONING_TOKENS on the other
    # vendors' requests, no cache reads, at batch prices.
    typical_usd: float
    # The same, with each Claude request's instructions read from the cache, as on the dev runs,
    # where they were about 80% of every request; the other vendors still pay full input.
    cached_usd: float
    # Every request retried once, each attempt with max_tokens of output, no cache reads, at
    # batch prices. Real spend cannot exceed it.
    ceiling_usd: float
    # Typical billed tokens per pair, what self-consistency's k is matched on.
    billed_per_pair: float


def _input_tokens(params: Any) -> int:
    return round(len(json.dumps(params, ensure_ascii=False)) / classify.CHARS_PER_TOKEN)


def estimate_arm(
    items: Sequence[PairInput],
    arm: Arm,
    config: QuestionConfig,
    guide: str,
    replay_dir: Path,
    prices: Prices,
    k: int | None,
) -> ArmEstimate:
    """What finishing `arm` on the pairs would cost, before anything is sent."""
    run = attempt(arm, config, guide, k)
    rate = prices.batch_discount / 1_000_000
    left = requests = 0
    typical = cached = ceiling = billed = 0.0
    for item in items:
        try:
            run(item, ModelClient(replay_dir))
            continue
        except ReplayMiss:
            pass
        left += 1
        simulated = Simulated()
        run(item, simulated)
        for params in simulated.sent:
            p = model_prices(model_of(params), prices)
            tokens = _input_tokens(params)
            hidden = TYPICAL_REASONING_TOKENS if vendor_of(params) is not None else 0
            out = TYPICAL_VISIBLE_TOKENS + hidden
            requests += 1
            typical += (tokens * p.input + out * p.output) * rate
            reused = _input_tokens(params["system"]) if "system" in params else 0
            fresh = tokens - reused
            cached += (fresh * p.input + reused * p.cache_read + out * p.output) * rate
            ceiling += ((2 * tokens + MAX_TOKENS) * p.input + 2 * MAX_TOKENS * p.output) * rate
            billed += tokens + out
    return ArmEstimate(
        arm=arm,
        pairs=left,
        requests=requests,
        typical_usd=typical,
        cached_usd=cached,
        ceiling_usd=ceiling,
        billed_per_pair=billed / left if left else 0.0,
    )


def describe(estimate: ArmEstimate) -> str:
    return (
        f"{estimate.arm.value}: {estimate.pairs} unfinished pairs, {estimate.requests:,} requests; "
        f"typical ${estimate.typical_usd:.2f}, ${estimate.cached_usd:.2f} with Claude's cache "
        f"as on dev, at most ${estimate.ceiling_usd:.2f} at batch prices; about "
        f"{estimate.billed_per_pair:,.0f} billed tokens per pair. Nothing was sent."
    )


def provisional_k(items: Sequence[PairInput], config: QuestionConfig, guide: str) -> int:
    """Self-consistency's k from the dry run's typical tokens, before council-ranked's are
    measured: an estimate, which the measured k replaces."""

    def billed(arm: Arm) -> float:
        total = 0.0
        for item in items:
            simulated = Simulated()
            attempt(arm, config, guide, None)(item, simulated)
            for params in simulated.sent:
                hidden = TYPICAL_REASONING_TOKENS if vendor_of(params) is not None else 0
                total += _input_tokens(params) + TYPICAL_VISIBLE_TOKENS + hidden
        return total

    return max(1, round(billed(Arm.COUNCIL_RANKED) / billed(Arm.SINGLE_MID)))
