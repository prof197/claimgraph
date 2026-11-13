"""The batch path: extraction in rounds of batches, recorded for replay, and the dry run."""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from test_extract import MISQUOTED, finding, output, response
from test_labels import ROOT, VITAMIN_D, synthetic_abstract

from claimgraph import batch
from claimgraph.batch import BatchOutcome, estimate, extract_batched
from claimgraph.extract import ModelChoice, extract_abstract, request
from claimgraph.llm import ModelClient, recording_files
from claimgraph.prices import load_prices
from claimgraph.retrieve import Abstract

PRICES = load_prices(ROOT / "prices.toml")
Responder = Callable[[Mapping[str, Any]], dict[str, Any]]


def titled(title: str, pmid: str = "999000001") -> Abstract:
    return synthetic_abstract(pmid).model_copy(update={"title": title})


VALID = response(json.dumps(output()))


class FakeBatches:
    """A batch API that answers each request through a responder."""

    def __init__(self, respond: Responder, fail: set[str] | None = None) -> None:
        self.respond = respond
        self.fail = fail or set()
        self.batches: list[dict[str, Mapping[str, Any]]] = []

    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        self.batches.append(dict(requests))
        return f"msgbatch_synthetic_{len(self.batches)}"

    def collect(self, batch_id: str) -> BatchOutcome:
        requests = self.batches[int(batch_id.rsplit("_", 1)[1]) - 1]
        failed = {k for k, p in requests.items() if p["messages"][0]["content"] in self.fail}
        return BatchOutcome(
            responses={k: self.respond(p) for k, p in requests.items() if k not in failed},
            failures=dict.fromkeys(failed, "errored: overloaded_error"),
        )


def by_title(answers: Mapping[str, list[dict[str, Any]]]) -> Responder:
    """Answers per abstract title, in the order its requests arrive."""

    def respond(params: Mapping[str, Any]) -> dict[str, Any]:
        title = params["messages"][0]["content"].split("\n")[0].removeprefix("Title: ")
        messages = params["messages"]
        # The first request, then the retry for invalid output, then the one for missed quotes.
        attempt = (
            0 if len(messages) == 1 else 2 if "word for word" in messages[-1]["content"] else 1
        )
        return answers[title][attempt]

    return respond


def test_rounds_of_batches_carry_each_abstract_through_its_retries(tmp_path: Path) -> None:
    abstracts = [titled("Valid at once."), titled("Invalid first.", "999000002")]
    sender = FakeBatches(
        by_title({"Valid at once.": [VALID], "Invalid first.": [response("not json"), VALID]})
    )

    run = extract_batched(abstracts, VITAMIN_D, tmp_path, ModelChoice.HAIKU, sender)

    # The first round sends both first requests; the second sends only the retry.
    assert [len(b) for b in sender.batches] == [2, 1]
    assert run.batches == 2
    assert run.missing == {}
    assert [r.study is not None for r in run.results] == [True, True]
    assert len(list(tmp_path.glob("*.json"))) == 3
    # One prompt version, so one copy of the shared instructions and schema.
    assert len(list((tmp_path / "shared").glob("*.json"))) == 1


def test_a_quote_miss_takes_a_third_round(tmp_path: Path) -> None:
    missed = response(json.dumps(output(findings=[finding(quote=MISQUOTED)])))
    sender = FakeBatches(by_title({"Misquoted.": [response("not json"), missed, VALID]}))

    run = extract_batched([titled("Misquoted.")], VITAMIN_D, tmp_path, ModelChoice.HAIKU, sender)

    assert run.batches == 3
    (result,) = run.results
    assert result.quote_misses == [MISQUOTED]
    assert result.quotes_dropped == 0


def test_a_batch_recording_replays_like_a_direct_one(tmp_path: Path) -> None:
    abstract = titled("Valid at once.")
    sender = FakeBatches(by_title({"Valid at once.": [VALID]}))
    run = extract_batched([abstract], VITAMIN_D, tmp_path, ModelChoice.HAIKU, sender)

    params = request(abstract, VITAMIN_D, ModelChoice.HAIKU)
    for name, text in recording_files(params, VALID).items():
        assert (tmp_path / name).read_text() == text
    assert extract_abstract(abstract, VITAMIN_D, ModelClient(tmp_path)) == run.results[0]


def test_replay_sends_nothing_and_reports_what_is_missing(tmp_path: Path) -> None:
    abstracts = [titled("Recorded."), titled("Not recorded.", "999000002")]
    extract_batched(
        abstracts[:1],
        VITAMIN_D,
        tmp_path,
        ModelChoice.HAIKU,
        FakeBatches(by_title({"Recorded.": [VALID]})),
    )

    run = extract_batched(abstracts, VITAMIN_D, tmp_path, ModelChoice.HAIKU, None)

    assert run.batches == 0
    assert [r.pmid for r in run.results] == ["999000001"]
    assert run.missing == {"999000002": "not recorded"}


def test_a_failed_request_is_reported_and_not_sent_again(tmp_path: Path) -> None:
    abstract = titled("Fails in the batch.")
    message = request(abstract, VITAMIN_D, ModelChoice.HAIKU)["messages"][0]["content"]
    sender = FakeBatches(by_title({}), fail={message})

    run = extract_batched([abstract], VITAMIN_D, tmp_path, ModelChoice.HAIKU, sender)

    assert run.batches == 1
    assert run.missing == {"999000001": "errored: overloaded_error"}
    assert list(tmp_path.glob("*.json")) == []


def test_identical_requests_are_sent_once(tmp_path: Path) -> None:
    twins = [titled("Same text."), titled("Same text.", "999000002")]
    sender = FakeBatches(by_title({"Same text.": [VALID]}))

    run = extract_batched(twins, VITAMIN_D, tmp_path, ModelChoice.HAIKU, sender)

    assert [len(b) for b in sender.batches] == [1]
    assert len(run.results) == 2


# The Anthropic batch sender, against a stand-in for the SDK client


class FakeSdk:
    def __init__(self, polls: int) -> None:
        self.polls = polls
        self.created: list[dict[str, Any]] = []
        outer = self

        class Batches:
            def create(self, requests: list[dict[str, Any]]) -> Any:
                outer.created = requests
                return SimpleNamespace(id="msgbatch_synthetic", processing_status="in_progress")

            def retrieve(self, batch_id: str) -> Any:
                outer.polls -= 1
                status = "ended" if outer.polls <= 0 else "in_progress"
                counts = SimpleNamespace(processing=outer.polls, succeeded=1, errored=1)
                return SimpleNamespace(id=batch_id, processing_status=status, request_counts=counts)

            def results(self, batch_id: str) -> list[Any]:
                message = SimpleNamespace(model_dump=lambda mode: VALID)
                error = SimpleNamespace(error=SimpleNamespace(type="overloaded_error"))
                return [
                    SimpleNamespace(
                        custom_id="a" * 64,
                        result=SimpleNamespace(type="succeeded", message=message),
                    ),
                    SimpleNamespace(
                        custom_id="b" * 64, result=SimpleNamespace(type="errored", error=error)
                    ),
                    SimpleNamespace(custom_id="c" * 64, result=SimpleNamespace(type="expired")),
                ]

        self.messages = SimpleNamespace(batches=Batches())


def test_the_api_submits_polls_and_sorts_the_results() -> None:
    sdk = FakeSdk(polls=3)
    sleeps: list[float] = []
    api = batch.AnthropicBatches(cast(Any, sdk), sleep=sleeps.append, poll_seconds=30)
    requests = {k * 64: {"model": "synthetic"} for k in "abc"}

    batch_id = api.submit(requests)
    outcome = api.collect(batch_id)

    assert batch_id == "msgbatch_synthetic"
    assert sdk.created == [{"custom_id": k, "params": p} for k, p in requests.items()]
    assert sleeps == [30, 30]
    assert outcome.responses == {"a" * 64: VALID}
    assert outcome.failures == {"b" * 64: "errored: overloaded_error", "c" * 64: "expired"}


def test_live_batches_need_an_api_key() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        batch.anthropic_batches({})


# The dry run


def test_the_estimate_counts_only_requests_not_yet_recorded(tmp_path: Path) -> None:
    abstracts = [titled("Recorded."), titled("Not recorded.", "999000002")]
    extract_batched(
        abstracts[:1],
        VITAMIN_D,
        tmp_path,
        ModelChoice.HAIKU,
        FakeBatches(by_title({"Recorded.": [VALID]})),
    )

    both = estimate(abstracts, VITAMIN_D, tmp_path / "empty", ModelChoice.HAIKU, PRICES)
    one = estimate(abstracts, VITAMIN_D, tmp_path, ModelChoice.HAIKU, PRICES)

    assert (both.abstracts, one.abstracts) == (2, 1)
    assert one.input_tokens * 2 == pytest.approx(both.input_tokens, rel=0.05)
    assert 0 < one.typical_usd < one.ceiling_usd
    assert both.typical_usd == pytest.approx(2 * one.typical_usd, rel=0.05)


def test_the_ceiling_bounds_three_requests_at_full_output(tmp_path: Path) -> None:
    (abstract,) = [titled("Not recorded.")]
    result = estimate([abstract], VITAMIN_D, tmp_path, ModelChoice.HAIKU, PRICES)
    params = request(abstract, VITAMIN_D, ModelChoice.HAIKU)
    haiku = PRICES.models["claude-haiku-5-5"]

    # Three requests, each at the full input price and max_tokens of output, at batch prices.
    floor = 3 * params["max_tokens"] * haiku.output / 1e6 * PRICES.batch_discount
    assert result.ceiling_usd > floor


def test_sonnet_costs_more_than_haiku(tmp_path: Path) -> None:
    abstracts = [titled("Not recorded.")]
    haiku = estimate(abstracts, VITAMIN_D, tmp_path, ModelChoice.HAIKU, PRICES)
    sonnet = estimate(abstracts, VITAMIN_D, tmp_path, ModelChoice.SONNET, PRICES)

    assert sonnet.typical_usd == pytest.approx(haiku.typical_usd * 20, rel=0.01)


def test_a_prompt_past_the_price_tier_is_refused(tmp_path: Path) -> None:
    huge = synthetic_abstract().model_copy(update={"title": "Synthetic. " * 40_000})

    with pytest.raises(ValueError, match="100000"):
        estimate([huge], VITAMIN_D, tmp_path, ModelChoice.HAIKU, PRICES)


# The extract stage from the command line


def run_extract(tmp_path: Path, *args: str) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    config = str(ROOT / "questions" / "vitamin-d-depression.toml")
    return CliRunner().invoke(
        cli.app,
        ["run", config, "--stage", "extract", "--dev", "--runs-dir", str(tmp_path), *args],
    )


def dev_sample(tmp_path: Path) -> Path:
    run_dir = tmp_path / "vitamin-d-depression"
    (run_dir / "dev").mkdir(parents=True)
    abstracts = [titled("First dev."), titled("Second dev.", "999000002")]
    (run_dir / "dev" / "abstracts.jsonl").write_text(
        "".join(a.model_dump_json() + "\n" for a in abstracts)
    )
    return run_dir


def never_send(env: Mapping[str, str], on_poll: Any = None) -> batch.BatchApi:
    raise AssertionError("nothing may be sent")


def test_the_dry_run_prices_the_dev_set_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_sample(tmp_path)
    monkeypatch.setattr(batch, "anthropic_batches", never_send)

    result = run_extract(tmp_path, "--dry-run", "--live")

    assert result.exit_code == 0, result.output
    assert "dry run: 2 abstracts to send to claude-haiku-5-5" in result.output
    assert "at batch prices" in result.output


def test_a_live_dev_run_records_through_batches_then_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = dev_sample(tmp_path)
    sender = FakeBatches(by_title({"First dev.": [VALID], "Second dev.": [VALID]}))
    monkeypatch.setattr(batch, "anthropic_batches", lambda env, on_poll=None: sender)

    unrecorded = run_extract(tmp_path)
    live = run_extract(tmp_path, "--live")
    monkeypatch.setattr(batch, "anthropic_batches", never_send)
    replayed = run_extract(tmp_path)

    assert unrecorded.exit_code == 1
    assert "0 extracted, 2 missing" in unrecorded.output
    assert live.exit_code == 0, live.output
    assert [len(b) for b in sender.batches] == [2]
    assert len(list((run_dir / "replay" / "extraction" / "haiku").glob("*.json"))) == 2
    assert replayed.exit_code == 0, replayed.output
    assert "2 extracted, 0 missing" in replayed.output
    manifest = json.loads((run_dir / "extract" / "manifest.json").read_text())
    assert manifest["runs"]["dev-haiku"]["extracted"] == 2
    assert manifest["runs"]["dev-haiku"]["requests"] == 2
    lines = (run_dir / "extract" / "dev-haiku.jsonl").read_text().splitlines()
    assert [json.loads(line)["pmid"] for line in lines] == ["999000001", "999000002"]


def test_a_dev_run_without_abstracts_says_what_to_run(tmp_path: Path) -> None:
    result = run_extract(tmp_path)

    assert result.exit_code == 1
    assert "run --stage retrieve first" in result.output


def test_stale_covers_recordings_and_shared_files_nothing_uses(tmp_path: Path) -> None:
    abstract = titled("Valid at once.")
    run = extract_batched(
        [abstract],
        VITAMIN_D,
        tmp_path,
        ModelChoice.HAIKU,
        FakeBatches(by_title({"Valid at once.": [VALID]})),
    )
    # Left by an earlier prompt version: a recording and its shared instructions.
    (tmp_path / f"{'0' * 64}.json").write_text("{}\n")
    (tmp_path / "shared" / f"{'1' * 64}.json").write_text("{}\n")

    assert batch.stale(tmp_path, run.used) == [f"{'0' * 64}.json", f"shared/{'1' * 64}.json"]


# Picking up a stopped run


class Stopped(FakeBatches):
    """Sends its batch, then is stopped while waiting, as an interrupted run is."""

    def collect(self, batch_id: str) -> BatchOutcome:
        raise KeyboardInterrupt


class Resumed(FakeBatches):
    """Holds the batches a stopped run sent; must not send any more."""

    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        raise AssertionError("a stopped run's requests must not be sent again")


def test_a_stopped_run_leaves_its_batch_and_the_next_run_collects_it(tmp_path: Path) -> None:
    abstracts = [titled("First."), titled("Second.", "999000002")]
    respond = by_title({"First.": [VALID], "Second.": [VALID]})
    stopped = Stopped(respond)

    with pytest.raises(KeyboardInterrupt):
        extract_batched(abstracts, VITAMIN_D, tmp_path, ModelChoice.HAIKU, stopped)
    assert batch.pending_batches(tmp_path) == ["msgbatch_synthetic_1"]
    assert list(tmp_path.glob("*.json")) == []

    resumed = Resumed(respond)
    resumed.batches = stopped.batches
    run = extract_batched(abstracts, VITAMIN_D, tmp_path, ModelChoice.HAIKU, resumed)

    assert run.resumed == 1
    assert run.batches == 0
    assert run.missing == {}
    assert len(run.results) == 2
    assert batch.pending_batches(tmp_path) == []
    summary = batch.summarize(run, "claude-haiku-5-5", PRICES)
    assert "collected 1 batches a stopped run left" in batch.describe(run, summary)[0]


def test_a_collected_result_no_request_needs_is_not_recorded(tmp_path: Path) -> None:
    old = Stopped(by_title({"An older prompt.": [VALID]}))
    with pytest.raises(KeyboardInterrupt):
        extract_batched([titled("An older prompt.")], VITAMIN_D, tmp_path, ModelChoice.HAIKU, old)
    current = FakeBatches(by_title({"An older prompt.": [VALID], "Current.": [VALID]}))
    current.batches = list(old.batches)

    run = extract_batched([titled("Current.")], VITAMIN_D, tmp_path, ModelChoice.HAIKU, current)

    # The old batch is collected and dropped; the current request is sent as usual.
    assert run.resumed == 1
    assert run.batches == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_replay_never_collects(tmp_path: Path) -> None:
    (tmp_path / batch.PENDING_FILE).write_text("msgbatch_synthetic_1\n")

    run = extract_batched([titled("First.")], VITAMIN_D, tmp_path, ModelChoice.HAIKU, None)

    assert run.resumed == 0
    assert batch.pending_batches(tmp_path) == ["msgbatch_synthetic_1"]


def test_the_summary_counts_what_was_dropped_and_seen(tmp_path: Path) -> None:
    from claimgraph.config import OUTSIDE_DOMAIN
    from claimgraph.design_tree import full_answers
    from claimgraph.models import Design

    anxiety = finding(
        quote="Synthetic depression scores fell more with synthetic vitamin D than with placebo "
        "(p = 0.01).",
        outcome_reported="synthetic anxiety",
        domain_outcome=OUTSIDE_DOMAIN,
    )
    single_arm = response(
        json.dumps(
            output(
                design_answers=full_answers(Design.CASE_SERIES),
                findings=[finding(), anxiety],
                other_outcomes=["synthetic fatigue"],
            )
        )
    )
    run = extract_batched(
        [titled("Single arm.")],
        VITAMIN_D,
        tmp_path,
        ModelChoice.HAIKU,
        FakeBatches(by_title({"Single arm.": [single_arm]})),
    )

    summary = batch.summarize(run, "claude-haiku-5-5", PRICES)

    assert (summary.findings, summary.outside_domain, summary.other_outcomes) == (1, 1, 2)
    assert summary.single_arm == 1
    assert summary.cost_usd > 0


def test_the_summary_counts_trial_findings_within_one_group(tmp_path: Path) -> None:
    from claimgraph.design_tree import full_answers
    from claimgraph.models import Design

    within = finding(
        quote="Synthetic depression scores fell more with synthetic vitamin D than with placebo "
        "(p = 0.01).",
        comparison="within_one_group",
    )
    trial = response(
        json.dumps(output(design_answers=full_answers(Design.RCT), findings=[finding(), within]))
    )
    run = extract_batched(
        [titled("Trial.")],
        VITAMIN_D,
        tmp_path,
        ModelChoice.HAIKU,
        FakeBatches(by_title({"Trial.": [trial]})),
    )

    summary = batch.summarize(run, "claude-haiku-5-5", PRICES)

    assert (summary.findings, summary.within_or_pooled) == (1, 1)
    assert "1 more dropped as within one group or pooled in a trial" in "\n".join(
        batch.describe(run, summary)
    )


def test_an_entry_written_before_a_field_was_added_does_not_block_the_next(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    older = {"model": "claude-haiku-5-5", "findings": 1}
    path.write_text(json.dumps({"runs": {"gold-haiku": older, "dev-haiku": older}}))
    run = extract_batched(
        [titled("First.")],
        VITAMIN_D,
        tmp_path / "replay",
        ModelChoice.HAIKU,
        FakeBatches(by_title({"First.": [response(json.dumps(output()))]})),
    )
    summary = batch.summarize(run, "claude-haiku-5-5", PRICES)

    batch.write_manifest(path, "dev-haiku", summary)

    runs = json.loads(path.read_text())["runs"]
    # Other entries are kept as they were written; this run's entry is replaced whole.
    assert runs["gold-haiku"] == older
    assert runs["dev-haiku"] == json.loads(summary.model_dump_json())
