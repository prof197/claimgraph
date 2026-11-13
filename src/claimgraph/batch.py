"""Extraction through the Message Batches API, and the dry run that prices it first.

Batches cost half as much but answer later, while an abstract's retries each depend on the
response before them. So extraction runs in rounds: each round replays every abstract as far as
the recordings go, sends the requests that come next in one batch, and records the responses.
At most three rounds are needed, for the first request, the retry for invalid output and the
retry for missed quotes. Batch responses are recorded exactly as direct ones are, so a run
replays the same however its responses were obtained.
"""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import anthropic

from claimgraph.config import QuestionConfig
from claimgraph.extract import SETTINGS, ExtractionResult, ModelChoice, extract_abstract, request
from claimgraph.llm import SHARED_DIR, ModelClient, ReplayMiss, request_key
from claimgraph.models import Record
from claimgraph.prices import Prices, cost, model_prices
from claimgraph.retrieve import Abstract

MAX_ROUNDS = 3
POLL_SECONDS = 30


class BatchOutcome(Record):
    # Responses by request key, as Messages API JSON.
    responses: dict[str, dict[str, Any]]
    # Requests the batch did not answer, by key: errored, canceled or expired.
    failures: dict[str, str]


# Sends one batch of requests, keyed by request key, and waits for its results.
type BatchSender = Callable[[Mapping[str, Mapping[str, Any]]], BatchOutcome]


def batch_sender(
    client: anthropic.Anthropic,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = POLL_SECONDS,
    on_poll: Callable[[str], None] = lambda _: None,
) -> BatchSender:
    def send(requests: Mapping[str, Mapping[str, Any]]) -> BatchOutcome:
        # The request key is 64 hex digits, which a batch accepts as a custom_id.
        entries: Any = [{"custom_id": key, "params": params} for key, params in requests.items()]
        created = client.messages.batches.create(requests=entries)
        status = created.processing_status
        while status != "ended":
            sleep(poll_seconds)
            current = client.messages.batches.retrieve(created.id)
            status = current.processing_status
            counts = current.request_counts
            on_poll(
                f"batch {created.id}: {counts.processing} processing, "
                f"{counts.succeeded} succeeded, {counts.errored} errored"
            )
        responses: dict[str, dict[str, Any]] = {}
        failures: dict[str, str] = {}
        for item in client.messages.batches.results(created.id):
            result = item.result
            if result.type == "succeeded":
                responses[item.custom_id] = result.message.model_dump(mode="json")
            elif result.type == "errored":
                failures[item.custom_id] = f"errored: {result.error.error.type}"
            else:
                failures[item.custom_id] = result.type
        return BatchOutcome(responses=responses, failures=failures)

    return send


def anthropic_batches(
    env: Mapping[str, str], on_poll: Callable[[str], None] = lambda _: None
) -> BatchSender:
    if not env.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set; live runs need it (see .env.example)")
    return batch_sender(anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"]), on_poll=on_poll)


class BatchRun(Record):
    results: list[ExtractionResult]
    # Abstracts without a complete extraction, by PMID, and why.
    missing: dict[str, str]
    batches: int
    # Keys of the recordings the final replay used, so unused ones can be found.
    used: frozenset[str]


def extract_batched(
    abstracts: Sequence[Abstract],
    config: QuestionConfig,
    replay_dir: Path,
    choice: ModelChoice,
    send: BatchSender | None,
) -> BatchRun:
    """Every abstract's extraction from the recordings, sending batches for what is missing.

    With no sender, this is replay: nothing is sent and unrecorded abstracts are reported.
    """
    failures: dict[str, str] = {}
    batches = 0
    while True:
        client = ModelClient(replay_dir)
        results: list[ExtractionResult] = []
        missing: dict[str, str] = {}
        pending: dict[str, Mapping[str, Any]] = {}
        for abstract in abstracts:
            try:
                results.append(extract_abstract(abstract, config, client, choice))
            except ReplayMiss as miss:
                missing[abstract.pmid] = failures.get(miss.key, "not recorded")
                if miss.key not in failures:
                    pending[miss.key] = miss.params
        if not pending or send is None or batches == MAX_ROUNDS:
            return BatchRun(
                results=results, missing=missing, batches=batches, used=frozenset(client.used)
            )
        outcome = send(pending)
        batches += 1
        for key, response in outcome.responses.items():
            client.record(pending[key], response)
        failures |= outcome.failures


def stale(replay_dir: Path, used: frozenset[str]) -> list[str]:
    """Files no current request uses, left behind by an earlier prompt or schema: recordings,
    and shared instructions no used recording names. Paths are relative to replay_dir."""
    recordings = sorted(replay_dir.glob("*.json"))
    named = {
        json.loads(p.read_text())["request"].get("shared") for p in recordings if p.stem in used
    }
    unused = [p.name for p in recordings if p.stem not in used]
    shared = sorted((replay_dir / SHARED_DIR).glob("*.json"))
    return unused + [f"{SHARED_DIR}/{p.name}" for p in shared if p.stem not in named]


# The dry run

# Characters per token, set low on purpose: about four for English text, less on the current
# tokenizer, which counts about 30% more tokens for the same text.
CHARS_PER_TOKEN = 3.0
# Output tokens assumed for a typical first response, until a run measures them.
TYPICAL_OUTPUT_TOKENS = 1500
# A retry's note on top of the first request and the output it shows back.
RETRY_NOTE_TOKENS = 300


class Estimate(Record):
    model: str
    # Abstracts whose first request has no recording yet.
    abstracts: int
    input_tokens: int
    # One request each with typical output and no cache reads, at batch prices.
    typical_usd: float
    # Every abstract taking all three requests, each with max_tokens of output and no cache
    # reads, at batch prices. Real spend cannot exceed it.
    ceiling_usd: float


def _tokens(params: Mapping[str, Any]) -> int:
    sent = {k: params[k] for k in ("system", "messages", "output_config")}
    return round(len(json.dumps(sent, ensure_ascii=False)) / CHARS_PER_TOKEN)


def estimate(
    abstracts: Sequence[Abstract],
    config: QuestionConfig,
    replay_dir: Path,
    choice: ModelChoice,
    prices: Prices,
) -> Estimate:
    """What sending the unrecorded first requests would cost, before anything is sent."""
    model = SETTINGS[choice].model
    p = model_prices(model, prices)
    rate = prices.batch_discount / 1_000_000
    abstracts_left = input_tokens = 0
    typical = ceiling = 0.0
    for abstract in abstracts:
        params = request(abstract, config, choice)
        if (replay_dir / f"{request_key(params)}.json").exists():
            continue
        tokens = _tokens(params)
        out = int(params["max_tokens"])
        retry = tokens + out + RETRY_NOTE_TOKENS
        if retry > prices.max_prompt_tokens:
            raise ValueError(
                f"PMID {abstract.pmid}: a request could pass {prices.max_prompt_tokens} prompt "
                "tokens, where the price file no longer applies"
            )
        abstracts_left += 1
        input_tokens += tokens
        typical += (tokens * p.input + TYPICAL_OUTPUT_TOKENS * p.output) * rate
        ceiling += ((tokens + 2 * retry) * p.input + 3 * out * p.output) * rate
    return Estimate(
        model=model,
        abstracts=abstracts_left,
        input_tokens=input_tokens,
        typical_usd=typical,
        ceiling_usd=ceiling,
    )


def describe(run: BatchRun, prices: Prices) -> list[str]:
    """The run's counts and what its recorded responses cost at batch prices, as lines."""
    results = run.results
    studies = [r.study for r in results if r.study is not None]
    usage = [(u, r.model) for r in results for u in r.usage]
    spend = sum(cost(u, model, prices, batch=True) for u, model in usage)
    return [
        f"{len(results)} extracted, {len(run.missing)} missing; "
        f"{sum(r.study is None for r in results)} invalid after the retry",
        f"{sum(len(s.findings) for s in studies)} findings; quotes: "
        f"{sum(r.quotes_checked for r in results)} checked, "
        f"{sum(len(r.quote_misses) for r in results)} missed, "
        f"{sum(r.quotes_dropped for r in results)} dropped",
        f"{len(usage)} requests, {run.batches} batches sent now; tokens: "
        f"{sum(u.input_tokens for u, _ in usage)} input, "
        f"{sum(u.cache_creation_input_tokens for u, _ in usage)} cache write, "
        f"{sum(u.cache_read_input_tokens for u, _ in usage)} cache read, "
        f"{sum(u.output_tokens for u, _ in usage)} output; ${spend:.4f} at batch prices",
    ]


def describe_estimate(result: Estimate, prices: Prices) -> str:
    return (
        f"dry run: {result.abstracts} abstracts to send to {result.model}, about "
        f"{result.input_tokens} input tokens; typical ${result.typical_usd:.4f}, at most "
        f"${result.ceiling_usd:.4f} at batch prices (prices checked {prices.checked})"
    )
