"""The outcome proposal and the mapping file.

Every outcome string and response here is synthetic, served through the replay client from a
fake transport.
"""

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from test_extract import Scripted, finding, output, response
from test_labels import ROOT, VITAMIN_D, synthetic_abstract

from claimgraph import normalize
from claimgraph.extract import ExtractionResult, ModelChoice, read_extraction
from claimgraph.llm import Mode, ModelClient, Reply, Usage
from claimgraph.models import NormalizedOutcome
from claimgraph.prices import load_prices

STRINGS = ["synthetic mood score", "synthetic low mood", "synthetic mood disorder onset"]


def result(*outcomes: str, pmid: str = "999000001") -> ExtractionResult:
    findings = [finding(outcome_reported=o) for o in outcomes]
    text = json.dumps(output(findings=findings))
    study = read_extraction(reply(text), synthetic_abstract(), VITAMIN_D) if outcomes else None
    return ExtractionResult(
        pmid=pmid,
        model="synthetic",
        study=study,
        errors=[],
        quotes_checked=0,
        quote_misses=[],
        quotes_dropped=0,
        usage=[],
    )


def reply(text: str, stop_reason: str = "end_turn") -> Reply:
    usage = Usage(
        input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0
    )
    return Reply(
        id="msg_synthetic", model="synthetic", stop_reason=stop_reason, text=text, usage=usage
    )


def outcome(name: str, domain: str = "depressive symptoms", better: bool = False) -> dict[str, Any]:
    return {"name": name, "domain": domain, "higher_is_better": better}


def assign(*pairs: tuple[int, str]) -> list[dict[str, Any]]:
    return [{"number": n, "outcome": o} for n, o in pairs]


# STRINGS sorted: 1 synthetic low mood, 2 synthetic mood disorder onset, 3 synthetic mood score.
VALID = {
    "outcomes": [
        outcome("synthetic mood symptoms"),
        outcome("synthetic mood disorder onset", "depression diagnosis or incidence"),
    ],
    "assignments": assign(
        (1, "synthetic mood symptoms"),
        (2, "synthetic mood disorder onset"),
        (3, "synthetic mood symptoms"),
    ),
}


def read(proposal: dict[str, Any], stop_reason: str = "end_turn") -> list[NormalizedOutcome]:
    return normalize.read_proposal(
        reply(json.dumps(proposal), stop_reason), sorted(STRINGS), VITAMIN_D
    )


def propose(tmp_path: Path, *responses: dict[str, Any]) -> tuple[normalize.Proposal, Scripted]:
    transport = Scripted(*responses)
    client = ModelClient(tmp_path, Mode.LIVE, transport=transport)
    return normalize.propose(sorted(STRINGS), VITAMIN_D, client), transport


# Collecting the strings


def test_the_strings_are_each_findings_outcome_once_in_order() -> None:
    results = [
        result("synthetic mood score", "synthetic low mood"),
        result("synthetic low mood", pmid="999000002"),
        # Invalid after the retry, so no study and no findings.
        result(pmid="999000003"),
    ]

    assert normalize.outcome_strings(results) == ["synthetic low mood", "synthetic mood score"]


# The request


def test_the_request_numbers_every_string_and_pins_the_model_settings() -> None:
    params = normalize.request(sorted(STRINGS), VITAMIN_D, ModelChoice.SONNET)

    assert params["model"] == "claude-sonnet-5-5"
    assert params["max_tokens"] == normalize.MAX_TOKENS
    assert params["thinking"] == {"type": "between_tools"}
    assert not {"temperature", "top_p", "top_k"} & params.keys()
    (message,) = params["messages"]
    assert message["content"].splitlines() == [
        "1. synthetic low mood",
        "2. synthetic mood disorder onset",
        "3. synthetic mood score",
    ]
    assert params["output_config"]["format"]["schema"] == normalize.response_schema(VITAMIN_D)


def test_each_outcome_names_one_domain_entry_or_none() -> None:
    schema = normalize.response_schema(VITAMIN_D)

    domain = schema["properties"]["outcomes"]["items"]["properties"]["domain"]
    assert domain["enum"] == [*VITAMIN_D.outcome_domain, "outside the domain"]


def test_the_prompt_is_filled_from_the_config() -> None:
    prompt = normalize.system_prompt(VITAMIN_D)

    assert VITAMIN_D.title in prompt
    for entry in VITAMIN_D.outcome_domain:
        assert entry in prompt


def test_the_max_tokens_stay_under_the_non_streaming_limit() -> None:
    # The SDK refuses a non-streamed request whose max_tokens could take over ten minutes.
    assert normalize.MAX_TOKENS <= 21_333


# Reading the proposal


def test_a_valid_proposal_becomes_unreviewed_outcomes_named_by_their_strings() -> None:
    assert read(VALID) == [
        NormalizedOutcome(
            name="synthetic mood symptoms",
            members=["synthetic low mood", "synthetic mood score"],
            higher_is_better=False,
            reviewed=False,
        ),
        NormalizedOutcome(
            name="synthetic mood disorder onset",
            members=["synthetic mood disorder onset"],
            higher_is_better=False,
            reviewed=False,
        ),
    ]


def test_the_largest_outcome_comes_first_and_an_empty_one_is_left_out() -> None:
    proposal = {
        "outcomes": [outcome("onset"), outcome("unused"), outcome("symptoms")],
        "assignments": assign((1, "symptoms"), (2, "Onset"), (3, "symptoms")),
    }

    assert [o.name for o in read(proposal)] == ["symptoms", "onset"]


ALL = ((1, "a"), (2, "a"), (3, "a"))


@pytest.mark.parametrize(
    ("proposal", "problem"),
    [
        (
            {"outcomes": [outcome("a")], "assignments": assign((1, "a"), (3, "a"))},
            "string 2 is not assigned",
        ),
        (
            {"outcomes": [outcome("a")], "assignments": assign(*ALL, (2, "a"))},
            "string 2 is assigned more than once",
        ),
        (
            {"outcomes": [outcome("a")], "assignments": assign(*ALL, (4, "a"))},
            "string 4 is not on the list",
        ),
        (
            {"outcomes": [outcome("a")], "assignments": assign((1, "a"), (2, "b"), (3, "a"))},
            "string 2 is assigned to 'b', not an outcome",
        ),
        (
            {
                "outcomes": [outcome("Synthetic A"), outcome("synthetic a")],
                "assignments": assign(*ALL),
            },
            "outcome name 'synthetic a' is used twice",
        ),
        ({"outcomes": [outcome(" ")], "assignments": []}, "outcomes.0.name is blank"),
        (
            {"outcomes": [outcome("a", "synthetic mood")], "assignments": assign(*ALL)},
            "outcome 'a' has the domain 'synthetic mood'",
        ),
    ],
)
def test_an_invalid_proposal_names_each_problem(proposal: dict[str, Any], problem: str) -> None:
    with pytest.raises(normalize.InvalidProposal, match=problem):
        read(proposal)


@pytest.mark.parametrize(
    ("text", "stop_reason", "problem"),
    [
        ("not json", "end_turn", "not JSON"),
        (json.dumps(VALID), "max_tokens", "stopped: max_tokens"),
        ("", "refusal", "stopped: refusal"),
    ],
)
def test_output_that_cannot_be_read_is_invalid(text: str, stop_reason: str, problem: str) -> None:
    with pytest.raises(normalize.InvalidProposal, match=problem):
        normalize.read_proposal(reply(text, stop_reason), sorted(STRINGS), VITAMIN_D)


# The call


def test_a_valid_first_response_needs_no_retry(tmp_path: Path) -> None:
    proposal, transport = propose(tmp_path, response(json.dumps(VALID)))

    assert proposal.outcomes is not None
    assert len(proposal.outcomes) == 2
    assert (proposal.strings, proposal.errors, len(transport.requests)) == (3, [], 1)
    assert proposal.model == "claude-sonnet-5-5"
    assert len(proposal.usage) == 1


def test_an_invalid_proposal_gets_one_retry_that_names_the_problem(tmp_path: Path) -> None:
    missing = {"outcomes": [outcome("a")], "assignments": assign((1, "a"), (3, "a"))}
    proposal, transport = propose(
        tmp_path, response(json.dumps(missing)), response(json.dumps(VALID))
    )

    assert proposal.outcomes is not None
    assert proposal.errors == ["string 2 is not assigned"]
    first, retry = transport.requests
    assert retry["messages"][0] == first["messages"][0]
    assert retry["messages"][1] == {"role": "assistant", "content": json.dumps(missing)}
    assert "string 2 is not assigned" in retry["messages"][2]["content"]


def test_a_proposal_invalid_twice_has_no_outcomes(tmp_path: Path) -> None:
    proposal, transport = propose(tmp_path, response("not json"), response("still not json"))

    assert proposal.outcomes is None
    assert len(proposal.errors) == 2
    assert len(transport.requests) == 2


def test_a_recorded_proposal_replays_without_a_call(tmp_path: Path) -> None:
    propose(tmp_path, response(json.dumps(VALID)))
    client = ModelClient(tmp_path, Mode.REPLAY)

    replayed = normalize.propose(sorted(STRINGS), VITAMIN_D, client)

    assert replayed.outcomes is not None
    assert len(replayed.outcomes) == 2


# The estimate


def test_the_estimate_prices_an_unrecorded_proposal_and_its_retry(tmp_path: Path) -> None:
    prices = load_prices(ROOT / "prices.toml")

    estimate = normalize.estimate(sorted(STRINGS), VITAMIN_D, tmp_path, ModelChoice.SONNET, prices)

    assert estimate.model == "claude-sonnet-5-5"
    assert estimate.strings == 3
    assert not estimate.recorded
    assert 0 < estimate.typical_usd < estimate.ceiling_usd
    # The ceiling is the first request and its retry, each at max_tokens, at standard prices.
    out = 2 * normalize.MAX_TOKENS * prices.models["claude-sonnet-5-5"].output / 1_000_000
    assert estimate.ceiling_usd > out


def test_a_recorded_proposal_costs_nothing_to_rerun(tmp_path: Path) -> None:
    propose(tmp_path, response(json.dumps(VALID)))
    prices = load_prices(ROOT / "prices.toml")

    estimate = normalize.estimate(sorted(STRINGS), VITAMIN_D, tmp_path, ModelChoice.SONNET, prices)

    assert estimate.recorded
    assert (estimate.typical_usd, estimate.ceiling_usd) == (0, 0)


# The mapping file

AWKWARD = 'synthetic "quoted" score \\ ≥16, two\nlines\ttab'


def test_the_mapping_file_reads_back_as_written() -> None:
    outcomes = [
        NormalizedOutcome(
            name="synthetic symptoms",
            members=[AWKWARD, "synthetic low mood"],
            higher_is_better=False,
            reviewed=False,
        ),
        NormalizedOutcome(
            name="synthetic remission",
            members=["synthetic remission"],
            higher_is_better=True,
            reviewed=True,
        ),
    ]

    text = normalize.mapping_toml(outcomes, ["A synthetic header.", "Second line."])

    assert text.startswith("# A synthetic header.\n# Second line.\n")
    assert normalize.read_mapping(text).outcomes == outcomes
    assert tomllib.loads(text)["outcomes"][0]["reviewed"] is False


def test_a_string_in_two_outcomes_is_refused() -> None:
    text = normalize.mapping_toml(
        [
            NormalizedOutcome(name="a", members=["s"], higher_is_better=False, reviewed=False),
            NormalizedOutcome(name="b", members=["s"], higher_is_better=False, reviewed=False),
        ],
        [],
    )

    with pytest.raises(ValueError, match="'s' is in more than one outcome"):
        normalize.read_mapping(text)


# The CLI


def corpus_results(tmp_path: Path) -> Path:
    out = tmp_path / "runs" / "vitamin-d-depression" / "extract" / "corpus-sonnet.jsonl"
    out.parent.mkdir(parents=True)
    results = [result(*STRINGS[:2]), result(STRINGS[2], pmid="999000002")]
    out.write_text("".join(r.model_dump_json() + "\n" for r in results))
    return out


def run_normalize(tmp_path: Path, *args: str) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    config = str(ROOT / "questions" / "vitamin-d-depression.toml")
    return CliRunner().invoke(
        cli.app,
        [
            "run",
            config,
            "--stage",
            "normalize",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--replay-dir",
            str(tmp_path / "replay"),
            "--mappings-dir",
            str(tmp_path / "mappings"),
            *args,
        ],
    )


def test_the_dry_run_prices_the_proposal_and_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_results(tmp_path)
    monkeypatch.setattr(normalize, "anthropic_transport", never_called)

    result = run_normalize(tmp_path, "--dry-run", "--live")

    assert result.exit_code == 0, result.output
    assert "dry run: 3 outcome strings to send to claude-sonnet-5-5 in one request" in result.output
    assert not (tmp_path / "mappings").exists()


def never_called(env: Any) -> Any:
    raise AssertionError("a dry run must not build a transport")


def test_a_replay_without_a_recording_exits_1(tmp_path: Path) -> None:
    corpus_results(tmp_path)

    result = run_normalize(tmp_path)

    assert result.exit_code == 1
    assert "record it with --live" in result.output
    assert not (tmp_path / "mappings").exists()


def test_a_live_proposal_writes_an_unreviewed_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_results(tmp_path)
    transport = Scripted(response(json.dumps(VALID)))
    monkeypatch.setattr(normalize, "anthropic_transport", lambda env: transport)

    result = run_normalize(tmp_path, "--live")

    assert result.exit_code == 0, result.output
    mapping = normalize.load_mapping(tmp_path / "mappings" / "vitamin-d-depression.toml")
    assert [o.name for o in mapping.outcomes] == [
        "synthetic mood symptoms",
        "synthetic mood disorder onset",
    ]
    assert not any(o.reviewed for o in mapping.outcomes)
    assert "3 outcome strings in 2 outcomes" in result.output
    recordings = tmp_path / "replay" / "vitamin-d-depression" / "normalization" / "sonnet"
    assert len(list(recordings.glob("*.json"))) == 1


def test_an_existing_mapping_is_not_overwritten(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    mapping = tmp_path / "mappings" / "vitamin-d-depression.toml"
    mapping.parent.mkdir()
    mapping.write_text("# reviewed by hand\n")

    result = run_normalize(tmp_path, "--live")

    assert result.exit_code == 0, result.output
    assert "not overwritten" in result.output
    assert mapping.read_text() == "# reviewed by hand\n"


def test_normalizing_needs_the_corpus_extraction(tmp_path: Path) -> None:
    result = run_normalize(tmp_path)

    assert result.exit_code == 1
    assert "run --stage extract first" in result.output
