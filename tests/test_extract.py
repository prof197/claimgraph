"""Extraction: the shared prompt, the response schema, and validation with one retry.

Every response here is synthetic, served through the replay client from a fake transport.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from test_labels import ROOT, VITAMIN_D, synthetic_abstract

from claimgraph import batch, extract
from claimgraph.config import QuestionConfig, load_config
from claimgraph.design_tree import QUESTIONS, READINGS, answers_for
from claimgraph.dimensions import NOT_REACHED
from claimgraph.extract import (
    RULES,
    InvalidOutput,
    ModelChoice,
    check_schema,
    extract_abstract,
    read_extraction,
    request,
    response_schema,
    system_prompt,
)
from claimgraph.labels import read_gold
from claimgraph.llm import Mode, ModelClient, Reply, Usage
from claimgraph.models import Design, Effect
from claimgraph.retrieve import Abstract

CREATINE = load_config(ROOT / "questions" / "creatine-cognition.toml")
GOLD = ROOT / "evals" / "gold" / "vitamin-d-depression"
QUOTE = "Synthetic remission rates did not differ between groups."


def answers(design: Design = Design.RCT) -> dict[str, str]:
    return {q.key: NOT_REACHED for q in QUESTIONS} | answers_for(design)


def finding(**overrides: Any) -> dict[str, Any]:
    return {
        "claim": "Synthetic remission did not differ.",
        "quote": QUOTE,
        "outcome_reported": "synthetic remission",
        "effect": "no_change",
        "is_primary": False,
        "is_subgroup": False,
        "subgroup": None,
        "population": "synthetic adults",
        "intervention": "synthetic vitamin D3",
        "comparator": "placebo",
        "baseline_vitamin_d_status": "not_reported",
    } | overrides


def output(**overrides: Any) -> dict[str, Any]:
    return {
        "design_answers": answers(),
        "sample_size": 120,
        "review_summary": None,
        "other_outcomes": [],
        "findings": [finding()],
    } | overrides


def reply(text: str, stop_reason: str = "end_turn") -> Reply:
    usage = Usage(
        input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0
    )
    return Reply(
        id="msg_synthetic", model="synthetic", stop_reason=stop_reason, text=text, usage=usage
    )


def response(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_synthetic",
        "model": "synthetic",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 900, "output_tokens": 300},
    }


class Scripted:
    """A transport that answers each request with the next scripted response."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[Mapping[str, Any]] = []

    def __call__(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self.requests.append(params)
        return self.responses.pop(0)


def run(
    tmp_path: Path, *responses: dict[str, Any], abstract: Abstract | None = None
) -> tuple[extract.ExtractionResult, Scripted]:
    transport = Scripted(*responses)
    client = ModelClient(tmp_path, Mode.LIVE, transport=transport)
    result = extract_abstract(abstract or synthetic_abstract(), VITAMIN_D, client)
    return result, transport


# The request


def test_the_request_pins_its_settings_and_sends_no_sampling_parameters() -> None:
    params = request(synthetic_abstract(), VITAMIN_D, ModelChoice.HAIKU)

    assert params["model"] == "claude-haiku-5-5"
    assert params["thinking"] == {"type": "disabled"}
    assert params["output_config"]["effort"] == "medium"
    assert params["output_config"]["format"] == {
        "type": "json_schema",
        "schema": response_schema(VITAMIN_D),
    }
    assert not {"temperature", "top_p", "top_k", "tools", "tool_choice"} & params.keys()
    # The instructions are the cached prefix; the abstract follows them.
    (system,) = params["system"]
    assert system == {
        "type": "text",
        "text": system_prompt(VITAMIN_D),
        "cache_control": {"type": "ephemeral"},
    }
    (message,) = params["messages"]
    assert message["role"] == "user"
    assert synthetic_abstract().title in message["content"]
    assert synthetic_abstract().text in message["content"]


def test_sonnet_turns_thinking_off_its_own_way() -> None:
    params = request(synthetic_abstract(), VITAMIN_D, ModelChoice.SONNET)

    assert params["model"] == "claude-sonnet-5-5"
    assert params["thinking"] == {"type": "between_tools"}
    assert params["output_config"]["effort"] == "medium"


def test_the_prefix_is_the_same_for_every_abstract() -> None:
    first = request(synthetic_abstract("999000001"), VITAMIN_D, ModelChoice.HAIKU)
    second = request(synthetic_abstract("999000002"), VITAMIN_D, ModelChoice.HAIKU)

    assert first["system"] == second["system"]
    assert first["output_config"] == second["output_config"]


# The prompt


def test_the_prompt_states_every_rule() -> None:
    prompt = system_prompt(VITAMIN_D)

    for rule in RULES:
        assert rule in prompt


def test_design_md_lists_the_prompt_rules_word_for_word() -> None:
    text = (ROOT / "DESIGN.md").read_text()
    block = text.split("filled in from the question config.")[1].split("The rules:\n")[1]
    bullets = re.split(r"\n  - ", "\n" + block.split("\n- ")[0].rstrip())[1:]
    listed = [" ".join(b.split()).replace("`", "") for b in bullets]

    assert listed == list(RULES)


def test_the_prompt_is_filled_from_the_config() -> None:
    prompt = system_prompt(VITAMIN_D)

    assert VITAMIN_D.title in prompt
    for outcome in VITAMIN_D.outcome_domain:
        assert outcome in prompt
    for dimension in VITAMIN_D.dimensions:
        assert dimension.name in prompt
        assert dimension.definition in prompt


def test_the_prompt_asks_every_design_question_with_its_reading() -> None:
    prompt = system_prompt(VITAMIN_D)

    for question in QUESTIONS:
        assert question.text in prompt
        for option in question.options:
            assert option.value in prompt
    for reading in READINGS.values():
        assert reading in prompt
    assert set(READINGS) <= {q.key for q in QUESTIONS}


def test_the_shared_prompt_names_no_question_but_its_own() -> None:
    # Everything specific to vitamin D comes from its config, so another question's prompt
    # carries none of it.
    prompt = system_prompt(CREATINE).casefold()

    for word in ("vitamin", "depress", "25(oh)d", "ng/ml", "phq", "anhedonia"):
        assert word not in prompt


def test_no_gold_sentence_appears_in_the_prompt() -> None:
    prompt = " ".join(system_prompt(VITAMIN_D).split())

    for entry in read_gold(GOLD):
        for section in entry.abstract.sections:
            for sentence in re.split(r"(?<=[.!?])\s+", section.text):
                if len(sentence) > 40:
                    assert " ".join(sentence.split()) not in prompt


# The response schema


def biggest_config(kind: str) -> QuestionConfig:
    dimensions = [
        {
            "name": f"synthetic_{n}",
            "definition": "Synthetic.",
            "type": kind,
            "refines": "population",
        }
        | ({"unit": "mg"} if kind == "number" else {})
        for n in range(5)
    ]
    return QuestionConfig.model_validate(VITAMIN_D.model_dump() | {"dimensions": dimensions})


@pytest.mark.parametrize(
    "config",
    [VITAMIN_D, CREATINE, biggest_config("text"), biggest_config("number")],
    ids=["vitamin-d", "creatine", "five-text-dimensions", "five-number-dimensions"],
)
def test_every_schema_fits_the_api_limits(config: QuestionConfig) -> None:
    assert check_schema(response_schema(config)) == []


def test_the_schema_drops_what_the_api_does_not_enforce() -> None:
    text = json.dumps(response_schema(VITAMIN_D))

    for keyword in ("exclusiveMinimum", "pattern", "title"):
        assert f'"{keyword}"' not in text
    # The property named "quote" and the descriptions survive.
    assert '"quote"' in text
    assert '"description"' in text


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


NULLABLE = {"anyOf": [{"type": "string"}, {"type": "null"}]}


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        (obj({"a": {"type": "string"}}) | {"additionalProperties": True}, "additionalProperties"),
        (obj({f"p{n}": {"type": "string"} for n in range(25)}, required=[]), "25 optional"),
        (obj({f"p{n}": NULLABLE for n in range(17)}), "17 union"),
        (
            obj({"a": {"type": ["string", "null"]}} | {f"p{n}": NULLABLE for n in range(16)}),
            "17 union",
        ),
        (obj({"a": {"type": "integer", "minimum": 1}}), "minimum"),
        (
            obj({"a": {"$ref": "#/$defs/Node"}})
            | {"$defs": {"Node": obj({"next": {"$ref": "#/$defs/Node"}})}},
            "recursive",
        ),
    ],
    ids=["open-object", "optional", "unions", "type-list-union", "constraint", "recursion"],
)
def test_the_schema_check_names_each_problem(schema: dict[str, Any], problem: str) -> None:
    (found,) = check_schema(schema)

    assert problem in found


def test_a_schema_within_the_limits_passes() -> None:
    schema = obj({f"p{n}": {"type": "string"} for n in range(23)}, required=[])

    assert check_schema(schema | {"properties": schema["properties"] | {"u": NULLABLE}}) == []


# Reading a response


def test_a_valid_response_becomes_a_study_with_findings() -> None:
    study = read_extraction(reply(json.dumps(output())), synthetic_abstract(), VITAMIN_D)

    assert study.design is Design.RCT
    assert study.design_from is extract.DesignSource.ANSWERS
    assert study.design_answers == answers_for(Design.RCT)
    (stored,) = study.findings
    assert stored.finding_id == "999000001-1"
    assert stored.dimensions == {"baseline_vitamin_d_status": "not_reported"}
    assert stored.effect is Effect.NO_CHANGE


def test_closed_values_match_whatever_their_case() -> None:
    upper = output(
        design_answers={k: v.upper() for k, v in answers().items()},
        findings=[finding(effect="No_Change", baseline_vitamin_d_status="BELOW_20_NG_ML")],
    )

    study = read_extraction(reply(json.dumps(upper)), synthetic_abstract(), VITAMIN_D)

    assert study.design is Design.RCT
    assert study.findings[0].effect is Effect.NO_CHANGE
    assert study.findings[0].dimensions["baseline_vitamin_d_status"] == "below_20_ng_ml"


def tagged(*publication_types: str) -> Abstract:
    return synthetic_abstract().model_copy(update={"publication_types": list(publication_types)})


@pytest.mark.parametrize(
    ("types", "design"),
    [
        (["Journal Article", "Randomized Controlled Trial"], Design.RCT),
        (["Systematic Review", "Meta-Analysis"], Design.META_ANALYSIS),
        (["Scoping Review"], Design.SYSTEMATIC_REVIEW),
    ],
)
def test_a_publication_type_that_names_a_design_sets_it(types: list[str], design: Design) -> None:
    cohort = output(design_answers=answers(Design.COHORT))

    study = read_extraction(reply(json.dumps(cohort)), tagged(*types), VITAMIN_D)

    assert study.design is design
    assert study.design_from is extract.DesignSource.PUBLICATION_TYPES
    # The answers are kept, so a disagreement with the tag stays visible.
    assert study.design_answers == answers_for(Design.COHORT)


@pytest.mark.parametrize(
    "types",
    [
        ["Clinical Trial"],
        ["Clinical Trial", "Journal Article"],
        ["Clinical Trial, Phase II"],
        ["Controlled Clinical Trial"],
        ["Observational Study"],
        ["Review"],
        [],
    ],
)
def test_these_publication_types_name_no_design(types: list[str]) -> None:
    assert extract.tagged_design(types) is None


def test_review_alone_names_no_design() -> None:
    narrative = output(design_answers=answers(Design.NARRATIVE_REVIEW), findings=[])

    study = read_extraction(reply(json.dumps(narrative)), tagged("Review"), VITAMIN_D)

    assert study.design is Design.NARRATIVE_REVIEW
    assert study.design_from is extract.DesignSource.ANSWERS


def test_a_review_keeps_its_summary_and_no_findings() -> None:
    review = output(review_summary="Synthetic reviews disagree.")

    study = read_extraction(reply(json.dumps(review)), tagged("Meta-Analysis"), VITAMIN_D)

    assert study.findings == []
    assert study.review_findings_dropped == 1
    assert study.review_summary == "Synthetic reviews disagree."


def test_a_study_that_is_not_a_review_has_no_summary() -> None:
    study = read_extraction(
        reply(json.dumps(output(review_summary="Synthetic."))), synthetic_abstract(), VITAMIN_D
    )

    assert study.review_summary is None


def test_a_primary_subgroup_result_is_kept_as_a_subgroup_and_counted() -> None:
    both = finding(is_primary=True, is_subgroup=True, subgroup="synthetic women")

    study = read_extraction(
        reply(json.dumps(output(findings=[both]))), synthetic_abstract(), VITAMIN_D
    )

    (stored,) = study.findings
    assert (stored.is_primary, stored.is_subgroup) == (False, True)
    assert study.primary_subgroups == 1


@pytest.mark.parametrize(
    ("text", "stop_reason", "message"),
    [
        ("not json", "end_turn", "not valid JSON"),
        (json.dumps(output(findings=[finding(effect="worse")])), "end_turn", "findings.0.effect"),
        (json.dumps(output(sample_size=0)), "end_turn", "sample_size"),
        (json.dumps(output(findings=[finding(quote=" ")])), "end_turn", "findings.0.quote"),
        (
            json.dumps(output(design_answers=answers() | {"measured_before": "yes"})),
            "end_turn",
            "not asked once by_chance is yes: measured_before",
        ),
        (
            json.dumps(output(design_answers=answers() | {"by_chance": NOT_REACHED})),
            "end_turn",
            "answer by_chance",
        ),
        (json.dumps(output(findings=[finding(subgroup="synthetic men")])), "end_turn", "subgroup"),
        (json.dumps(output()), "refusal", "refusal"),
        (json.dumps(output())[:40], "max_tokens", "max_tokens"),
    ],
    ids=[
        "json",
        "enum",
        "sample-size",
        "blank-quote",
        "stray-answer",
        "missing-answer",
        "subgroup-name",
        "refusal",
        "max-tokens",
    ],
)
def test_invalid_output_is_named(text: str, stop_reason: str, message: str) -> None:
    with pytest.raises(InvalidOutput, match=re.escape(message)):
        read_extraction(reply(text, stop_reason), synthetic_abstract(), VITAMIN_D)


# One retry


def test_invalid_output_gets_one_retry_that_names_the_error(tmp_path: Path) -> None:
    result, transport = run(tmp_path, response("not json"), response(json.dumps(output())))

    assert result.study is not None
    assert len(result.errors) == 1
    assert "not valid JSON" in result.errors[0]
    assert len(result.usage) == 2
    first, retry = transport.requests
    # The retry is the first request plus the bad output and a note naming what was wrong.
    assert retry["messages"][:1] == first["messages"]
    assert retry["messages"][1] == {"role": "assistant", "content": "not json"}
    assert retry["messages"][2]["role"] == "user"
    assert "not valid JSON" in retry["messages"][2]["content"]
    assert {k: v for k, v in retry.items() if k != "messages"} == {
        k: v for k, v in first.items() if k != "messages"
    }


def test_an_empty_refusal_is_retried_without_an_assistant_turn(tmp_path: Path) -> None:
    result, transport = run(tmp_path, response("", "refusal"), response(json.dumps(output())))

    assert result.study is not None
    retry = transport.requests[1]
    assert [m["role"] for m in retry["messages"]] == ["user"]
    assert "refusal" in json.dumps(retry["messages"])


def test_a_second_failure_is_logged_and_counted(tmp_path: Path) -> None:
    result, transport = run(tmp_path, response("not json"), response("still not json"))

    assert result.study is None
    assert len(result.errors) == 2
    assert len(transport.requests) == 2


def test_a_valid_first_response_needs_no_retry(tmp_path: Path) -> None:
    result, transport = run(tmp_path, response(json.dumps(output())))

    assert result.study is not None
    assert result.errors == []
    assert len(transport.requests) == 1
    assert result.model == "claude-haiku-5-5"


def test_a_rerun_replays_both_attempts_with_no_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded, _ = run(tmp_path, response("not json"), response(json.dumps(output())))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    replayed = extract_abstract(synthetic_abstract(), VITAMIN_D, ModelClient(tmp_path))

    assert replayed == recorded


# Recording the gold set


def gold_question(tmp_path: Path) -> Path:
    from test_labels import gold_entry, write_gold

    gold_dir = tmp_path / "gold"
    write_gold(
        gold_dir / "vitamin-d-depression",
        gold_entry(),
        # A different text, so its request is not the first one's.
        gold_entry(
            abstract=synthetic_abstract("999000002").model_copy(
                update={"title": "A second synthetic title."}
            )
        ),
    )
    return gold_dir


def batches_from(transport: Scripted) -> Any:
    """A stand-in for the live batch sender that answers through a scripted transport."""

    def connect(env: Mapping[str, str], on_poll: Any = None) -> batch.BatchSender:
        def send(requests: Mapping[str, Mapping[str, Any]]) -> batch.BatchOutcome:
            return batch.BatchOutcome(
                responses={k: transport(p) for k, p in requests.items()}, failures={}
            )

        return send

    return connect


def record(tmp_path: Path, *args: str) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    config = str(ROOT / "questions" / "vitamin-d-depression.toml")
    return CliRunner().invoke(
        cli.app,
        [
            "gold",
            "record",
            config,
            "--gold-dir",
            str(tmp_path / "gold"),
            "--replay-dir",
            str(tmp_path / "replay"),
            *args,
        ],
    )


def test_recording_the_gold_set_then_checking_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold_question(tmp_path)
    transport = Scripted(response(json.dumps(output())), response(json.dumps(output(findings=[]))))
    monkeypatch.setattr(batch, "anthropic_batches", batches_from(transport))
    recordings = tmp_path / "replay" / "vitamin-d-depression" / "extraction" / "haiku"

    unrecorded = record(tmp_path)
    live = record(tmp_path, "--live")
    checked = record(tmp_path)

    assert unrecorded.exit_code == 1
    assert "0 extracted, 2 missing" in unrecorded.output
    assert live.exit_code == 0, live.output
    assert len(transport.requests) == 2
    assert len(list(recordings.glob("*.json"))) == 2
    assert checked.exit_code == 0, checked.output
    assert "2 extracted, 0 missing" in checked.output


def test_a_live_recording_removes_recordings_no_request_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold_question(tmp_path)
    transport = Scripted(response(json.dumps(output())), response(json.dumps(output())))
    monkeypatch.setattr(batch, "anthropic_batches", batches_from(transport))
    recordings = tmp_path / "replay" / "vitamin-d-depression" / "extraction" / "haiku"
    recordings.mkdir(parents=True)
    # Left behind by an earlier prompt: no current request has this key.
    (recordings / f"{'0' * 64}.json").write_text("{}\n")

    checked = record(tmp_path)
    live = record(tmp_path, "--live")

    assert "1 stale" in checked.output
    assert (recordings / f"{'0' * 64}.json").exists() is False
    assert live.exit_code == 0, live.output
    assert "removed 1 stale" in live.output


def test_each_model_records_in_its_own_place(tmp_path: Path) -> None:
    haiku = extract.gold_replay_dir(tmp_path, VITAMIN_D, ModelChoice.HAIKU)
    sonnet = extract.gold_replay_dir(tmp_path, VITAMIN_D, ModelChoice.SONNET)

    assert haiku != sonnet
    assert haiku.parent == sonnet.parent == tmp_path / "vitamin-d-depression" / "extraction"


# The verbatim check

MISQUOTED = "Synthetic remission did not differ between the groups."
SCORES = (
    "Synthetic depression scores fell more with synthetic vitamin D than with placebo (p = 0.01)."
)


def scores(**overrides: Any) -> dict[str, Any]:
    return finding(quote=SCORES, outcome_reported="synthetic depression score") | overrides


def test_verbatim_quotes_need_no_quote_retry(tmp_path: Path) -> None:
    result, transport = run(tmp_path, response(json.dumps(output(findings=[finding(), scores()]))))

    assert len(transport.requests) == 1
    assert (result.quotes_checked, result.quote_misses, result.quotes_dropped) == (2, [], 0)


def test_a_missed_quote_gets_one_retry_that_names_it(tmp_path: Path) -> None:
    missed = output(findings=[finding(quote=MISQUOTED), scores()])
    fixed = output(findings=[finding(), scores()])

    result, transport = run(tmp_path, response(json.dumps(missed)), response(json.dumps(fixed)))

    assert len(transport.requests) == 2
    note = transport.requests[1]["messages"][2]["content"]
    assert MISQUOTED in note
    assert SCORES not in note
    assert transport.requests[1]["messages"][1]["content"] == json.dumps(missed)
    assert result.study is not None
    assert [f.quote for f in result.study.findings] == [QUOTE, SCORES]
    assert (result.quotes_checked, result.quote_misses, result.quotes_dropped) == (
        2,
        [MISQUOTED],
        0,
    )
    # A quote miss is not invalid output.
    assert result.errors == []


def test_a_second_miss_drops_the_finding_and_counts_it(tmp_path: Path) -> None:
    missed = output(findings=[finding(quote=MISQUOTED), scores()])

    result, _ = run(tmp_path, response(json.dumps(missed)), response(json.dumps(missed)))

    assert result.study is not None
    assert [f.quote for f in result.study.findings] == [SCORES]
    assert result.quotes_dropped == 1


def test_an_invalid_quote_retry_keeps_the_first_output_less_its_misses(tmp_path: Path) -> None:
    missed = output(findings=[finding(quote=MISQUOTED), scores()])

    result, _ = run(tmp_path, response(json.dumps(missed)), response("not json"))

    assert result.study is not None
    assert [f.quote for f in result.study.findings] == [SCORES]
    assert result.quotes_dropped == 1
    assert len(result.errors) == 1


def test_a_validation_retry_and_a_quote_retry_each_happen_once(tmp_path: Path) -> None:
    missed = output(findings=[finding(quote=MISQUOTED)])

    result, transport = run(
        tmp_path,
        response("not json"),
        response(json.dumps(missed)),
        response(json.dumps(missed)),
    )

    assert len(transport.requests) == 3
    assert result.study is not None
    assert result.study.findings == []
    assert (result.quote_misses, result.quotes_dropped) == ([MISQUOTED], 1)


def test_curly_marks_and_line_breaks_are_not_misses(tmp_path: Path) -> None:
    curly = abstract_with("Synthetic “remission” rates did not differ\nbetween groups.")
    straight = finding(quote='Synthetic "remission" rates did not differ between groups.')

    result, transport = run(
        tmp_path, response(json.dumps(output(findings=[straight]))), abstract=curly
    )

    assert len(transport.requests) == 1
    assert result.study is not None
    # The quote is kept as the model wrote it; only the check normalizes.
    assert result.study.findings[0].quote == straight["quote"]


def abstract_with(text: str) -> Abstract:
    from claimgraph.retrieve import AbstractSection

    section = AbstractSection(label="RESULTS", category="RESULTS", text=text)
    return synthetic_abstract().model_copy(update={"sections": [section]})


def test_no_returned_finding_has_an_unchecked_quote(tmp_path: Path) -> None:
    from claimgraph.verbatim import quote_in

    missed = output(findings=[finding(quote=MISQUOTED), scores(), finding(quote="Synthetic.")])

    result, _ = run(tmp_path, response(json.dumps(missed)), response(json.dumps(missed)))

    assert result.study is not None
    assert all(quote_in(f.quote, synthetic_abstract()) for f in result.study.findings)
    assert result.quotes_dropped == 2
