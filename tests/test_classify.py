import json
from pathlib import Path
from typing import Any

import pytest
from test_compare import MAPPING, paper
from test_extract import Scripted, reply, response
from test_labeling_guide import runs

from claimgraph import classify
from claimgraph.classify import InvalidOutput, PairInput
from claimgraph.config import load_config
from claimgraph.llm import Mode, ModelClient, request_key
from claimgraph.models import Arm, Design, Effect, PairKind, Reason, Verdict
from claimgraph.pair_sample import Combination, DrawnPair
from claimgraph.prices import load_prices

ROOT = Path(__file__).parents[1]
CONFIG = load_config(ROOT / "questions" / "vitamin-d-depression.toml")
GUIDE = (ROOT / "evals" / "gold" / "LABELING.md").read_text()


def item() -> PairInput:
    a = paper("999000001", ("synthetic mood score", Effect.DECREASED, True, False))
    b = paper(
        "999000002", ("synthetic low mood", Effect.NO_CHANGE, True, False), design=Design.COHORT
    )
    findings = {f.finding_id: f for r in (a, b) if r.study for f in r.study.findings}
    pair = DrawnPair(
        outcome="synthetic symptoms",
        study_a="999000001",
        study_b="999000002",
        kind=PairKind.EFFECT_VS_NONE,
        combination=Combination.TRIAL_OBSERVATIONAL,
        finding_a="999000001-1",
        finding_b="999000002-1",
    )
    studies = {"999000001": (Design.RCT, 120), "999000002": (Design.COHORT, None)}
    (made,) = classify.pair_inputs([pair], findings, studies, MAPPING)
    return made


def output(**overrides: Any) -> str:
    fields: dict[str, Any] = {
        "rationale": "The trial enrolled depressed adults and the cohort a general population.",
        "verdict": "genuine",
        "reason": "population",
        "also_differs": ["design_or_bias"],
        "detail": "synthetic: depressed adults vs a general population",
    } | overrides
    return json.dumps(fields)


@pytest.mark.parametrize(
    ("arm", "model"),
    [(Arm.SINGLE_SMALL, "claude-haiku-5-5"), (Arm.SINGLE_MID, "claude-sonnet-5-5")],
)
def test_each_single_arm_runs_on_its_own_model(arm: Arm, model: str) -> None:
    assert classify.request(item(), CONFIG, GUIDE, arm)["model"] == model


def test_the_prompt_carries_the_guide_s_rules_but_not_its_file_format() -> None:
    system = classify.system_prompt(CONFIG, GUIDE)

    assert "**`verdict`** is about the results" in system
    assert all(f"`{v.value}`" in system for v in Verdict)
    assert all(f"`{r.value}`" in system for r in Reason)
    assert "[[pair]]" not in system
    assert "baseline_vitamin_d_status refines population" in system


def test_the_prompt_borrows_no_phrase_from_a_gold_abstract() -> None:
    gold_dir = ROOT / "evals" / "gold" / "vitamin-d-depression"
    gold: set[tuple[str, ...]] = set()
    for line in (gold_dir / "gold.jsonl").read_text().splitlines():
        record = json.loads(line)
        gold |= runs(" ".join(s["text"] for s in record["abstract"]["sections"]))

    assert not runs(classify.system_prompt(CONFIG, GUIDE)) & gold


def test_the_pair_shows_both_sides_by_letter_with_what_each_found() -> None:
    message = classify.user_message(item())

    assert "Outcome: synthetic symptoms (lower is better)" in message
    assert "## Study A\nFound: benefit" in message
    assert "## Study B\nFound: no effect" in message
    assert "Design: cohort study\nSample size: not reported" in message
    # The studies are judged on their fields, not their identity.
    assert "999000001" not in message


def test_the_rationale_comes_first_and_titles_are_left_out() -> None:
    schema = classify.response_schema()

    assert next(iter(schema["properties"])) == "rationale"
    # Required but nullable: an off_question pair has no reason.
    assert "reason" in schema["required"]
    assert {"type": "null"} in schema["properties"]["reason"]["anyOf"]
    assert "title" not in json.dumps(schema).replace('"title":', "X") or "title" not in schema


def test_a_valid_output_reads_as_the_pair_s_classification() -> None:
    read = classify.read_classification(reply(output()), "synthetic|1|2")

    assert read.pair_id == "synthetic|1|2"
    assert (read.verdict, read.reason) == (Verdict.GENUINE, Reason.POPULATION)


def test_an_off_question_pair_reads_with_no_reason() -> None:
    text = output(
        verdict="off_question", reason=None, also_differs=[], detail="exposure: synthetic trait"
    )

    read = classify.read_classification(reply(text), "synthetic|1|2")

    assert (read.verdict, read.reason, read.also_differs) == (Verdict.OFF_QUESTION, None, [])


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(output(also_differs=["population"]), id="also differs repeats the reason"),
        pytest.param(output(reason="dose"), id="unknown reason"),
        pytest.param(output(reason=None), id="a genuine pair with no reason"),
        pytest.param(output(verdict="apparent", reason="unclear"), id="apparent and unclear"),
        pytest.param(output(verdict="off_question", also_differs=[]), id="off question, a reason"),
        pytest.param("not json", id="not JSON"),
    ],
)
def test_an_invalid_output_is_refused(text: str) -> None:
    with pytest.raises(InvalidOutput):
        classify.read_classification(reply(text), "synthetic|1|2")


def test_a_cut_off_output_is_refused() -> None:
    with pytest.raises(InvalidOutput, match="cut off"):
        classify.read_classification(reply(output(), stop_reason="max_tokens"), "synthetic|1|2")


def test_an_invalid_output_gets_one_retry_and_then_replays(tmp_path: Path) -> None:
    live = ModelClient(tmp_path, Mode.LIVE, Scripted(response("{}"), response(output())))

    result = classify.classify_pair(item(), CONFIG, GUIDE, live, Arm.SINGLE_SMALL)

    assert result.classification is not None and len(result.errors) == 1
    assert len(result.usage) == 2
    replayed = classify.classify_pair(
        item(), CONFIG, GUIDE, ModelClient(tmp_path), Arm.SINGLE_SMALL
    )
    assert replayed == result


def test_two_invalid_outputs_leave_the_pair_unclassified(tmp_path: Path) -> None:
    live = ModelClient(tmp_path, Mode.LIVE, Scripted(response("{}"), response("{}")))

    result = classify.classify_pair(item(), CONFIG, GUIDE, live, Arm.SINGLE_MID)

    assert result.classification is None and len(result.errors) == 2


def test_the_estimate_prices_only_unrecorded_pairs(tmp_path: Path) -> None:
    prices = load_prices(ROOT / "prices.toml")

    before = classify.estimate([item()], CONFIG, GUIDE, Arm.SINGLE_SMALL, tmp_path, prices)
    live = ModelClient(tmp_path, Mode.LIVE, Scripted(response(output())))
    classify.classify_pair(item(), CONFIG, GUIDE, live, Arm.SINGLE_SMALL)
    after = classify.estimate([item()], CONFIG, GUIDE, Arm.SINGLE_SMALL, tmp_path, prices)

    assert before.pairs == 1 and 0 < before.typical_usd < before.ceiling_usd
    assert after.pairs == 0 and after.ceiling_usd == 0


def test_only_eval_recordings_go_where_they_are_committed(tmp_path: Path) -> None:
    from claimgraph.cli import Split, classification_replay

    committed, run_dir = tmp_path / "replay" / "q", tmp_path / "runs" / "q"

    assert classification_replay(Split.EVAL, Arm.SINGLE_MID, committed, run_dir) == (
        committed / "classification" / "single-mid"
    )
    dev = classification_replay(Split.DEV, Arm.SINGLE_MID, committed, run_dir)
    assert dev.is_relative_to(run_dir)


# The request keys of one synthetic pair under the classification prompt as tuned on dev.
FROZEN_KEYS = {
    Arm.SINGLE_SMALL: "d7f669a37366110d9397aacb8c72dcd09696b3101b5b35266627a68ec0367dc8",
    Arm.SINGLE_MID: "ce0e8c72caecdf5e96a1ff2e017a3d7b497b57590e87520c8a39d25fb607cec1",
}


@pytest.mark.parametrize("arm", list(FROZEN_KEYS))
def test_the_classification_prompt_is_frozen(arm: Arm) -> None:
    # Frozen after the second dev round, before any arm classified an eval pair. The prompt
    # carries the labeling guide's contradiction rules, so a change to those rules, the prompt,
    # the schema or the settings is a deliberate unfreeze: it needs the owner's approval and a
    # dry run, and then these keys change with it.
    assert request_key(classify.request(item(), CONFIG, GUIDE, arm)) == FROZEN_KEYS[arm]
