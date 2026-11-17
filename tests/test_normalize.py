"""The outcome proposal and the mapping file.

Every outcome string and response here is synthetic, served through the replay client from a
fake transport.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from test_extract import Scripted, finding, output, response
from test_labels import ROOT, VITAMIN_D, synthetic_abstract

from claimgraph import normalize, outcome_kinds
from claimgraph.config import load_config
from claimgraph.extract import ExtractionResult, ModelChoice, read_extraction
from claimgraph.llm import Mode, ModelClient, Reply, Usage, request_key
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


def test_normalizing_needs_the_corpus_extraction(tmp_path: Path) -> None:
    result = run_normalize(tmp_path)

    assert result.exit_code == 1
    assert "run --stage extract first" in result.output


# Checking the mapping


def mapped(*outcomes: tuple[str, list[str], bool]) -> normalize.OutcomeMapping:
    return normalize.OutcomeMapping(
        outcomes=[
            NormalizedOutcome(name=n, members=m, higher_is_better=False, reviewed=r)
            for n, m, r in outcomes
        ]
    )


def test_a_reviewed_mapping_of_every_string_passes() -> None:
    mapping = mapped(("a", STRINGS[:2], True), ("b", [STRINGS[2]], True))

    assert normalize.check_mapping(mapping, STRINGS) == []


def test_the_check_names_unreviewed_outcomes_and_unmapped_strings() -> None:
    mapping = mapped(("a", STRINGS[:1], True), ("b", [STRINGS[2]], False))

    assert normalize.check_mapping(mapping, STRINGS) == [
        "1 of 2 outcomes unreviewed: b",
        "1 of 3 strings unmapped: synthetic low mood",
    ]


def test_a_member_no_finding_reports_does_not_fail_the_check() -> None:
    # A reviewed name for a string the corpus no longer holds costs nothing and may return.
    mapping = mapped(("a", [*STRINGS, "synthetic retired name"], True))

    assert normalize.check_mapping(mapping, STRINGS) == []
    assert normalize.unused_members(mapping, STRINGS) == ["synthetic retired name"]


def check_cli(tmp_path: Path) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    config = str(ROOT / "questions" / "vitamin-d-depression.toml")
    return CliRunner().invoke(
        cli.app,
        [
            "mapping",
            "check",
            config,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--mappings-dir",
            str(tmp_path / "mappings"),
        ],
    )


def write_mapping(tmp_path: Path, mapping: normalize.OutcomeMapping) -> None:
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    path.write_text(normalize.mapping_toml(mapping.outcomes, ["Synthetic."]))


def test_mapping_check_passes_a_reviewed_complete_mapping(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    write_mapping(tmp_path, mapped(("a", STRINGS, True)))

    result = check_cli(tmp_path)

    assert result.exit_code == 0, result.output
    assert "1 outcomes, 1 reviewed; 3 strings, 0 unmapped" in result.output


def test_mapping_check_fails_an_unreviewed_mapping(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    write_mapping(tmp_path, mapped(("a", STRINGS, False)))

    result = check_cli(tmp_path)

    assert result.exit_code == 1
    assert "1 of 1 outcomes unreviewed: a" in result.output


def test_mapping_check_fails_without_a_mapping(tmp_path: Path) -> None:
    corpus_results(tmp_path)

    result = check_cli(tmp_path)

    assert result.exit_code == 1
    assert "run --stage normalize first" in result.output


def test_mapping_check_reports_a_malformed_mapping(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[[outcomes]]\nname = "a"\n')

    result = check_cli(tmp_path)

    assert result.exit_code == 1
    assert "is not a valid mapping" in result.output


# Proposing only new strings

REVIEWED = [
    NormalizedOutcome(
        name="synthetic mood symptoms",
        members=["synthetic low mood"],
        higher_is_better=False,
        reviewed=True,
    )
]
NEW = ["synthetic mood disorder onset", "synthetic mood score"]


def test_a_first_proposal_lists_only_the_strings() -> None:
    params = normalize.request(NEW, VITAMIN_D, ModelChoice.SONNET, existing=[])

    assert params == normalize.request(NEW, VITAMIN_D, ModelChoice.SONNET)


def test_a_rerun_shows_the_existing_outcomes_before_the_new_strings() -> None:
    params = normalize.request(NEW, VITAMIN_D, ModelChoice.SONNET, existing=REVIEWED)

    content = params["messages"][0]["content"]
    assert "- synthetic mood symptoms (lower is better)" in content
    assert content.endswith("1. synthetic mood disorder onset\n2. synthetic mood score")
    # The instructions and schema are the first proposal's.
    first = normalize.request(NEW, VITAMIN_D, ModelChoice.SONNET)
    assert params["system"] == first["system"]
    assert params["output_config"] == first["output_config"]


def test_new_strings_can_join_an_existing_outcome_or_a_new_one() -> None:
    proposal = {
        "outcomes": [outcome("synthetic onset", "depression diagnosis or incidence")],
        "assignments": assign((1, "synthetic onset"), (2, "Synthetic mood symptoms")),
    }

    outcomes = normalize.read_proposal(
        reply(json.dumps(proposal)), NEW, VITAMIN_D, existing=REVIEWED
    )

    assert outcomes == [
        NormalizedOutcome(
            name="synthetic onset",
            members=["synthetic mood disorder onset"],
            higher_is_better=False,
            reviewed=False,
        ),
        # Spelled as the mapping spells it, and unreviewed until its new member is reviewed.
        NormalizedOutcome(
            name="synthetic mood symptoms",
            members=["synthetic mood score"],
            higher_is_better=False,
            reviewed=False,
        ),
    ]


def test_a_new_outcome_may_not_reuse_an_existing_name() -> None:
    proposal = {
        "outcomes": [outcome("Synthetic mood symptoms")],
        "assignments": assign((1, "Synthetic mood symptoms"), (2, "Synthetic mood symptoms")),
    }

    with pytest.raises(normalize.InvalidProposal, match="already in the mapping"):
        normalize.read_proposal(reply(json.dumps(proposal)), NEW, VITAMIN_D, existing=REVIEWED)


def test_tables_with_one_name_are_one_outcome_reviewed_only_when_all_are() -> None:
    first = normalize.mapping_toml(REVIEWED, ["Synthetic."])
    added = NormalizedOutcome(
        name="Synthetic mood symptoms",
        members=["synthetic mood score"],
        higher_is_better=False,
        reviewed=False,
    )
    text = first + "\n" + normalize.mapping_toml([added], ["Added."])

    (merged,) = normalize.read_mapping(text).outcomes

    assert merged.name == "synthetic mood symptoms"
    assert merged.members == ["synthetic low mood", "synthetic mood score"]
    assert not merged.reviewed


def test_tables_with_one_name_must_agree_on_higher_is_better() -> None:
    added = REVIEWED[0].model_copy(update={"members": ["x"], "higher_is_better": True})
    text = normalize.mapping_toml([*REVIEWED, added], [])

    with pytest.raises(ValueError, match="disagree on higher_is_better"):
        normalize.read_mapping(text)


def test_a_rerun_appends_new_strings_and_keeps_the_reviewers_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_results(tmp_path)
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    reviewed_text = "# A reviewer's note.\n" + normalize.mapping_toml(REVIEWED, ["Synthetic."])
    path.write_text(reviewed_text)
    proposal = {
        "outcomes": [outcome("synthetic onset", "depression diagnosis or incidence")],
        "assignments": assign((1, "synthetic onset"), (2, "synthetic mood symptoms")),
    }
    transport = Scripted(response(json.dumps(proposal)))
    monkeypatch.setattr(normalize, "anthropic_transport", lambda env: transport)

    result = run_normalize(tmp_path, "--live")

    assert result.exit_code == 0, result.output
    (sent,) = transport.requests
    assert sent["messages"][0]["content"].endswith(
        "1. synthetic mood disorder onset\n2. synthetic mood score"
    )
    text = path.read_text()
    assert text.startswith(reviewed_text)
    mapping = normalize.load_mapping(path)
    assert [(o.name, len(o.members), o.reviewed) for o in mapping.outcomes] == [
        ("synthetic mood symptoms", 2, False),
        ("synthetic onset", 1, False),
    ]
    assert "2 new outcome strings: 1 into existing outcomes, 1 into new ones" in result.output


def test_a_rerun_with_every_string_mapped_sends_nothing(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    whole = [REVIEWED[0].model_copy(update={"members": sorted(STRINGS)})]
    path.write_text(normalize.mapping_toml(whole, ["Synthetic."]))
    before = path.read_text()

    result = run_normalize(tmp_path, "--live")

    assert result.exit_code == 0, result.output
    assert "every outcome string is mapped; nothing to propose" in result.output
    assert path.read_text() == before


def test_a_rerun_dry_run_prices_only_the_new_strings(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    path.write_text(normalize.mapping_toml(REVIEWED, ["Synthetic."]))

    result = run_normalize(tmp_path, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "dry run: 2 outcome strings" in result.output


# Strings the review drops as out of domain


DROPPED = normalize.OutcomeMapping(
    outcomes=[
        NormalizedOutcome(
            name="synthetic mood symptoms",
            members=["synthetic low mood", "synthetic mood score"],
            higher_is_better=False,
            reviewed=True,
        )
    ],
    out_of_domain=["synthetic mood disorder onset"],
)


def test_the_out_of_domain_list_reads_back_as_written() -> None:
    text = normalize.mapping_toml(DROPPED.outcomes, ["Synthetic."], DROPPED.out_of_domain)

    assert normalize.read_mapping(text) == DROPPED
    # TOML keys after a table belong to it, so the list comes before the first table.
    assert text.index("out_of_domain = [") < text.index("[[outcomes]]")


def test_a_string_dropped_as_out_of_domain_counts_as_mapped() -> None:
    assert normalize.unmapped(DROPPED, STRINGS) == []
    assert normalize.check_mapping(DROPPED, STRINGS) == []


def test_a_string_cannot_be_both_an_outcome_member_and_out_of_domain() -> None:
    with pytest.raises(ValueError, match="both in an outcome and out of domain"):
        normalize.OutcomeMapping(outcomes=DROPPED.outcomes, out_of_domain=["synthetic low mood"])


def test_findings_on_out_of_domain_strings_are_counted_as_dropped() -> None:
    results = [result(*STRINGS[:2]), result(STRINGS[2], STRINGS[2], pmid="999000002")]

    # Two findings report the dropped string; the other two are kept.
    assert normalize.dropped_findings(results, DROPPED) == 2


def test_mapping_check_reports_the_findings_it_drops(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    path.write_text(normalize.mapping_toml(DROPPED.outcomes, ["Synthetic."], DROPPED.out_of_domain))

    result = check_cli(tmp_path)

    assert result.exit_code == 0, result.output
    assert "1 outcomes, 1 reviewed; 3 strings, 0 unmapped" in result.output
    assert "1 strings out of domain, dropping 1 findings" in result.output


def test_a_rerun_does_not_propose_out_of_domain_strings_again(tmp_path: Path) -> None:
    corpus_results(tmp_path)
    path = tmp_path / "mappings" / "vitamin-d-depression.toml"
    path.parent.mkdir(parents=True)
    path.write_text(normalize.mapping_toml(DROPPED.outcomes, ["Synthetic."], DROPPED.out_of_domain))

    result = run_normalize(tmp_path, "--live")

    assert result.exit_code == 0, result.output
    assert "every outcome string is mapped; nothing to propose" in result.output


# A string scored the other way from its outcome


OVERRIDE = """
[[polarity_overrides]]
string = "synthetic mood score"
higher_is_better = true
reviewed = false
why = "Synthetic: scored from 0 to 1, 1 being well."
"""


def test_a_polarity_override_reads_back_and_changes_one_string_only() -> None:
    text = normalize.mapping_toml(DROPPED.outcomes, ["Synthetic."], DROPPED.out_of_domain)

    mapping = normalize.read_mapping(text + OVERRIDE)

    assert mapping.polarity() == {"synthetic low mood": False, "synthetic mood score": True}
    assert mapping.polarity(overrides=False) == {
        "synthetic low mood": False,
        "synthetic mood score": False,
    }


def test_mapping_check_refuses_an_unreviewed_polarity_override() -> None:
    text = normalize.mapping_toml(DROPPED.outcomes, ["Synthetic."], DROPPED.out_of_domain)

    problems = normalize.check_mapping(normalize.read_mapping(text + OVERRIDE), STRINGS)

    assert problems == ["1 of 1 polarity overrides unreviewed: synthetic mood score"]


@pytest.mark.parametrize(
    ("string", "higher_is_better", "match"),
    [
        ("synthetic anything", True, "is not a member"),
        ("synthetic mood score", False, "agrees with"),
    ],
)
def test_a_polarity_override_is_on_a_member_and_differs_from_its_outcome(
    string: str, higher_is_better: bool, match: str
) -> None:
    override = normalize.PolarityOverride(
        string=string, higher_is_better=higher_is_better, reviewed=True, why="Synthetic."
    )

    with pytest.raises(ValueError, match=match):
        normalize.OutcomeMapping(outcomes=DROPPED.outcomes, polarity_overrides=[override])


def test_the_outcome_rules_give_no_labeled_string_its_kind() -> None:
    # The rules' examples are invented. A quoted phrase that is a labeled string, or any labeled
    # string of three words or more, would hand the model that string's kind, the named hard
    # cases among them. Short labeled strings such as "depression" are also rule and topic
    # words, so they are checked only where the prompt quotes them.
    for path in sorted((ROOT / "evals" / "gold").glob("*/outcome_kinds.toml")):
        config = load_config(ROOT / "questions" / f"{path.parent.name}.toml")
        prompt = normalize.system_prompt(config).casefold()
        labeled = {label.string.casefold() for label in outcome_kinds.load(path)}
        quoted = {q.casefold() for q in re.findall(r'"([^"]+)"', prompt)}

        assert config.outcome_rules, path.parent.name
        assert quoted & labeled == set(), path.parent.name
        assert [s for s in labeled if len(s.split()) >= 3 and s in prompt] == [], path.parent.name


def test_a_question_without_outcome_rules_groups_by_construct() -> None:
    config = VITAMIN_D.model_copy(update={"outcome_rules": ""})

    assert "Group by what is measured, not by how." in normalize.system_prompt(config)


# The request key of the recorded re-proposal under the outcome rules.
FROZEN_KEY = "67f6f870520a4cef91f79da8626ca5886496955b814eb773b56f3d016df67c38"


def test_the_normalization_prompt_is_frozen() -> None:
    # Frozen after the re-proposal under the outcome rules. A change to the prompt, schema,
    # rules or settings is a deliberate unfreeze: it needs the owner's approval and a new
    # recording, and then this key changes with it.
    config = load_config(ROOT / "questions" / "vitamin-d-depression.toml")
    path = ROOT / "evals" / "gold" / "vitamin-d-depression" / "outcome_kinds.toml"
    strings = sorted(label.string for label in outcome_kinds.load(path))

    assert request_key(normalize.request(strings, config)) == FROZEN_KEY
