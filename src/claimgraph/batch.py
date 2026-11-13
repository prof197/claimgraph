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
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import anthropic

from claimgraph.config import QuestionConfig
from claimgraph.extract import SETTINGS, ExtractionResult, ModelChoice, extract_abstract, request
from claimgraph.llm import SHARED_DIR, ModelClient, ReplayMiss, request_key
from claimgraph.models import Design, Record
from claimgraph.prices import Prices, cost, model_prices
from claimgraph.retrieve import Abstract, write_atomic

MAX_ROUNDS = 3
POLL_SECONDS = 30


class BatchOutcome(Record):
    # Responses by request key, as Messages API JSON.
    responses: dict[str, dict[str, Any]]
    # Requests the batch did not answer, by key: errored, canceled or expired.
    failures: dict[str, str]


class BatchApi(Protocol):
    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        """Send one batch of requests, keyed by request key; returns the batch's id."""
        ...

    def collect(self, batch_id: str) -> BatchOutcome:
        """Wait for a batch to end and return its results."""
        ...


class AnthropicBatches:
    def __init__(
        self,
        client: anthropic.Anthropic,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = POLL_SECONDS,
        on_poll: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.client = client
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.on_poll = on_poll

    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        # The request key is 64 hex digits, which a batch accepts as a custom_id.
        entries: Any = [{"custom_id": key, "params": params} for key, params in requests.items()]
        return self.client.messages.batches.create(requests=entries).id

    def collect(self, batch_id: str) -> BatchOutcome:
        while True:
            current = self.client.messages.batches.retrieve(batch_id)
            if current.processing_status == "ended":
                break
            counts = current.request_counts
            self.on_poll(
                f"batch {batch_id}: {counts.processing} processing, "
                f"{counts.succeeded} succeeded, {counts.errored} errored"
            )
            self.sleep(self.poll_seconds)
        responses: dict[str, dict[str, Any]] = {}
        failures: dict[str, str] = {}
        for item in self.client.messages.batches.results(batch_id):
            result = item.result
            if result.type == "succeeded":
                responses[item.custom_id] = result.message.model_dump(mode="json")
            elif result.type == "errored":
                failures[item.custom_id] = f"errored: {result.error.error.type}"
            else:
                failures[item.custom_id] = result.type
        return BatchOutcome(responses=responses, failures=failures)


def anthropic_batches(
    env: Mapping[str, str], on_poll: Callable[[str], None] = lambda _: None
) -> BatchApi:
    if not env.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set; live runs need it (see .env.example)")
    return AnthropicBatches(anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"]), on_poll=on_poll)


# Batches sent but not yet collected, one id per line, so a run stopped while it waits can be
# picked up by the next one instead of paying again.
PENDING_FILE = "pending-batches.txt"


def pending_batches(replay_dir: Path) -> list[str]:
    path = replay_dir / PENDING_FILE
    return path.read_text().split() if path.exists() else []


def _set_pending(replay_dir: Path, batch_ids: Sequence[str]) -> None:
    path = replay_dir / PENDING_FILE
    if batch_ids:
        replay_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(path, "".join(f"{b}\n" for b in batch_ids).encode())
    else:
        path.unlink(missing_ok=True)


class BatchRun(Record):
    results: list[ExtractionResult]
    # Abstracts without a complete extraction, by PMID, and why.
    missing: dict[str, str]
    batches: int
    # Batches an earlier, stopped run sent, collected here instead of sent again.
    resumed: int
    # Keys of the recordings the final replay used, so unused ones can be found.
    used: frozenset[str]


def extract_batched(
    abstracts: Sequence[Abstract],
    config: QuestionConfig,
    replay_dir: Path,
    choice: ModelChoice,
    api: BatchApi | None,
) -> BatchRun:
    """Every abstract's extraction from the recordings, sending batches for what is missing.

    With no batch API, this is replay: nothing is sent and unrecorded abstracts are reported.
    A live run first collects the batches a stopped run left, and records each result whose key
    is a request it needs, so no request is paid for twice.
    """
    failures: dict[str, str] = {}
    collected: dict[str, dict[str, Any]] = {}
    left = pending_batches(replay_dir) if api is not None else []
    for batch_id in left:
        assert api is not None
        collected |= api.collect(batch_id).responses
        _set_pending(replay_dir, pending_batches(replay_dir)[1:])
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
        found = [key for key in pending if key in collected]
        if found:
            for key in found:
                client.record(pending[key], collected.pop(key))
            continue
        if not pending or api is None or batches == MAX_ROUNDS:
            return BatchRun(
                results=results,
                missing=missing,
                batches=batches,
                resumed=len(left),
                used=frozenset(client.used),
            )
        batch_id = api.submit(pending)
        # Written before the wait, so a stop while waiting leaves the batch to collect.
        _set_pending(replay_dir, [*pending_batches(replay_dir), batch_id])
        outcome = api.collect(batch_id)
        batches += 1
        for key, response in outcome.responses.items():
            client.record(pending[key], response)
        _set_pending(replay_dir, [b for b in pending_batches(replay_dir) if b != batch_id])
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
# Output tokens for a typical response: the mean over the 67 requests of the first dev run on
# Haiku 5.5, 2026-11-13. The ceiling does not use it.
TYPICAL_OUTPUT_TOKENS = 590
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


class ExtractManifest(Record):
    """What one extraction run did: an entry of the extract stage's manifest."""

    model: str
    abstracts: int
    extracted: int
    missing: int
    # Abstracts whose output was still invalid after the retry.
    invalid: int
    findings: int
    # Findings the model marked outside the outcome domain, dropped from the findings.
    outside_domain: int
    # Trial findings within one group or pooled across groups, dropped from the findings.
    within_or_pooled: int
    # Outcome names outside the domain, across abstracts, the dropped findings' included.
    other_outcomes: int
    primary_subgroups: int
    review_findings_dropped: int
    # Case series with a finding on a given exposure: single-arm studies and historical controls.
    single_arm: int
    quotes_checked: int
    quote_misses: int
    quotes_dropped: int
    requests: int
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    # What the recorded responses cost at batch prices, from their token counts.
    cost_usd: float
    prices_checked: date


def summarize(run: BatchRun, model: str, prices: Prices) -> ExtractManifest:
    results = run.results
    studies = [r.study for r in results if r.study is not None]
    usage = [(u, r.model) for r in results for u in r.usage]
    return ExtractManifest(
        model=model,
        abstracts=len(results) + len(run.missing),
        extracted=len(results),
        missing=len(run.missing),
        invalid=sum(r.study is None for r in results),
        findings=sum(len(s.findings) for s in studies),
        outside_domain=sum(s.outside_domain for s in studies),
        within_or_pooled=sum(s.within_or_pooled for s in studies),
        other_outcomes=sum(len(s.other_outcomes) for s in studies),
        primary_subgroups=sum(s.primary_subgroups for s in studies),
        review_findings_dropped=sum(s.review_findings_dropped for s in studies),
        single_arm=sum(
            s.design is Design.CASE_SERIES and any(f.intervention.strip() for f in s.findings)
            for s in studies
        ),
        quotes_checked=sum(r.quotes_checked for r in results),
        quote_misses=sum(len(r.quote_misses) for r in results),
        quotes_dropped=sum(r.quotes_dropped for r in results),
        requests=len(usage),
        input_tokens=sum(u.input_tokens for u, _ in usage),
        cache_write_tokens=sum(u.cache_creation_input_tokens for u, _ in usage),
        cache_read_tokens=sum(u.cache_read_input_tokens for u, _ in usage),
        output_tokens=sum(u.output_tokens for u, _ in usage),
        cost_usd=sum(cost(u, m, prices, batch=True) for u, m in usage),
        prices_checked=prices.checked,
    )


def describe(run: BatchRun, summary: ExtractManifest) -> list[str]:
    """The run's counts and what its recorded responses cost at batch prices, as lines."""
    s = summary
    resumed = (
        [f"collected {run.resumed} batches a stopped run left, instead of sending them again"]
        if run.resumed
        else []
    )
    return [
        *resumed,
        f"{s.extracted} extracted, {s.missing} missing; {s.invalid} invalid after the retry",
        f"{s.findings} findings, {s.outside_domain} more dropped as outside the domain, "
        f"{s.within_or_pooled} more dropped as within one group or pooled in a trial; "
        f"{s.other_outcomes} other outcomes seen; {s.single_arm} single-arm",
        f"quotes: {s.quotes_checked} checked, {s.quote_misses} missed, {s.quotes_dropped} dropped",
        f"{s.requests} requests, {run.batches} batches sent now; tokens: {s.input_tokens} input, "
        f"{s.cache_write_tokens} cache write, {s.cache_read_tokens} cache read, "
        f"{s.output_tokens} output; ${s.cost_usd:.4f} at batch prices",
    ]


def write_manifest(path: Path, entry: str, summary: ExtractManifest) -> None:
    """Add or replace one run's entry, such as dev-haiku, in the extract stage's manifest."""
    entries: dict[str, ExtractManifest] = {}
    if path.exists():
        entries = _Manifest.model_validate_json(path.read_text()).runs
    entries[entry] = summary
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(
        path, _Manifest(runs=dict(sorted(entries.items()))).model_dump_json(indent=2).encode()
    )


class _Manifest(Record):
    runs: dict[str, ExtractManifest]


def describe_estimate(result: Estimate, prices: Prices) -> str:
    return (
        f"dry run: {result.abstracts} abstracts to send to {result.model}, about "
        f"{result.input_tokens} input tokens; typical ${result.typical_usd:.4f}, at most "
        f"${result.ceiling_usd:.4f} at batch prices (prices checked {prices.checked})"
    )
