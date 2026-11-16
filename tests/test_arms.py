"""The multi-call arms, end to end through fake batches that answer in each vendor's format."""

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from test_batch import PRICES
from test_classify import CONFIG, GUIDE, item, output
from test_extract import response
from test_vendors import google_response, openai_response

from claimgraph import arms, classify
from claimgraph.arms import ArmEstimate, Simulated
from claimgraph.batch import BatchOutcome, run_batched
from claimgraph.classify import Call, ClassifierOutput, ClassifyResult, PairInput
from claimgraph.llm import Usage, Vendor, request_key, vendor_of
from claimgraph.models import Arm, Reason, Verdict

type Answer = Callable[[Mapping[str, Any]], str]


def text_of(params: Mapping[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False)


def is_ranking(params: Mapping[str, Any]) -> bool:
    return '"ranking"' in text_of(params)


def letters(params: Mapping[str, Any]) -> list[str]:
    return sorted(set(re.findall(r"### Response ([A-H])", text_of(params))))


def default_answer(params: Mapping[str, Any]) -> str:
    if is_ranking(params):
        return json.dumps({"evaluation": "Synthetic evaluation.", "ranking": letters(params)})
    return output()


def in_format(params: Mapping[str, Any], text: str) -> dict[str, Any]:
    vendor = vendor_of(params)
    if vendor is Vendor.OPENAI:
        return openai_response(text)
    if vendor is Vendor.GOOGLE:
        return google_response(text)
    return response(text)


class VendorFake:
    """A batch API for every vendor at once, answering each request through `answer`."""

    def __init__(self, answer: Answer = default_answer) -> None:
        self.answer = answer
        self.rounds: list[dict[str, Mapping[str, Any]]] = []

    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        self.rounds.append(dict(requests))
        return str(len(self.rounds) - 1)

    def collect(self, batch_id: str) -> BatchOutcome:
        requests = self.rounds[int(batch_id)]
        return BatchOutcome(
            responses={k: in_format(p, self.answer(p)) for k, p in requests.items()},
            failures={},
        )


def run(tmp_path: Path, arm: Arm, fake: VendorFake | None, k: int | None = None) -> ClassifyResult:
    result = run_batched(
        [item()],
        lambda i: i.pair.pair_id,
        arms.attempt(arm, CONFIG, GUIDE, k),
        tmp_path,
        fake,
        max_rounds=arms.ROUNDS[arm],
    )
    (only,) = result.results
    return only


def test_the_council_asks_three_vendors_at_once_then_the_chair(tmp_path: Path) -> None:
    fake = VendorFake()

    result = run(tmp_path, Arm.COUNCIL, fake)

    first, chair = fake.rounds
    assert sorted(str(vendor_of(p)) for p in first.values()) == ["None", "google", "openai"]
    (chair_request,) = chair.values()
    assert chair_request["model"] == "claude-sonnet-5-5"
    assert letters(chair_request) == ["A", "B", "C"]
    assert "You chair a council." in chair_request["system"][0]["text"]
    assert result.classification is not None
    assert [c.model for c in result.calls] == [
        "claude-sonnet-5-5",
        "gpt-6.1-sol",
        "gemini-4-argon",
        "claude-sonnet-5-5",
    ]
    # Replay gives the same answer and sends nothing.
    assert run(tmp_path, Arm.COUNCIL, None) == result


def test_the_council_s_claude_member_sends_the_frozen_single_mid_request(tmp_path: Path) -> None:
    fake = VendorFake()
    run(tmp_path, Arm.COUNCIL, fake)

    single_mid = classify.request(item(), CONFIG, GUIDE, Arm.SINGLE_MID)
    assert request_key(single_mid) in fake.rounds[0]


def test_other_vendors_get_the_schema_inlined_and_their_lowest_reasoning() -> None:
    schema = classify.response_schema()
    system, user = "Synthetic system.", "Synthetic pair."
    sol, argon = arms.COUNCIL[1], arms.COUNCIL[2]

    openai = arms.member_request(sol, system, user, schema, "classification")["body"]
    google = arms.member_request(argon, system, user, schema, "classification")["body"]

    flat = openai["text"]["format"]["schema"]
    assert "$defs" not in json.dumps(flat) and "$ref" not in json.dumps(flat)
    assert flat["properties"]["verdict"]["enum"] == ["genuine", "apparent", "off_question"]
    assert openai["text"]["format"]["strict"] is True
    assert openai["reasoning"] == {"effort": "low"}
    assert google["config"]["thinking_config"] == {"thinking_level": "low"}
    assert google["config"]["response_json_schema"] == flat


def test_ranked_members_rank_the_anonymous_answers_before_the_chair(tmp_path: Path) -> None:
    fake = VendorFake()

    result = run(tmp_path, Arm.COUNCIL_RANKED, fake)

    _, rankings, chair = fake.rounds
    assert len(rankings) == 3 and all(is_ranking(p) for p in rankings.values())
    (chair_request,) = chair.values()
    assert "## Peer rankings" in chair_request["messages"][0]["content"]
    assert result.classification is not None
    assert len(result.calls) == 7


def test_a_ranking_that_skips_a_response_is_retried(tmp_path: Path) -> None:
    def answer(params: Mapping[str, Any]) -> str:
        # Wrong on the first try, right on the retry, which carries the note.
        if is_ranking(params) and "rank each" not in text_of(params):
            return json.dumps({"evaluation": "Synthetic.", "ranking": ["A"]})
        return default_answer(params)

    result = run(tmp_path, Arm.COUNCIL_RANKED, VendorFake(answer))

    assert result.errors.count("ranking: rank each of A, B, C exactly once") == 3
    assert result.classification is not None


def test_a_member_invalid_twice_leaves_the_others_to_the_chair(tmp_path: Path) -> None:
    def answer(params: Mapping[str, Any]) -> str:
        return "not json" if vendor_of(params) is Vendor.GOOGLE else default_answer(params)

    fake = VendorFake(answer)
    result = run(tmp_path, Arm.COUNCIL, fake)

    (chair_request,) = fake.rounds[-1].values()
    assert letters(chair_request) == ["A", "B"]
    assert len(result.errors) == 2
    assert result.classification is not None


def test_self_consistency_samples_k_times_and_takes_the_majority(tmp_path: Path) -> None:
    def answer(params: Mapping[str, Any]) -> str:
        n = int(params["metadata"]["user_id"].rsplit("-", 1)[1])
        apparent = output(verdict="apparent", reason="design_or_bias", also_differs=[])
        return apparent if n % 2 else output()

    fake = VendorFake(answer)
    result = run(tmp_path, Arm.SELF_CONSISTENCY, fake, k=5)

    (samples,) = fake.rounds
    assert len(samples) == 5
    assert result.classification is not None
    assert (result.classification.verdict, result.classification.reason) == (
        Verdict.GENUINE,
        Reason.POPULATION,
    )


def out(verdict: str, reason: str | None) -> ClassifierOutput:
    return ClassifierOutput.model_validate_json(
        output(verdict=verdict, reason=reason, also_differs=[], detail=f"{verdict} {reason}")
    )


def test_the_vote_takes_the_verdict_first_and_breaks_ties_by_order() -> None:
    votes = [
        out("apparent", "design_or_bias"),
        out("genuine", "population"),
        out("genuine", "design_or_bias"),
        out("apparent", "outcome_measure"),
        out("genuine", "design_or_bias"),
    ]

    assert arms.vote(votes).detail == "genuine design_or_bias"
    assert arms.vote(votes[:2]).detail == "apparent design_or_bias"


def test_self_moa_synthesizes_three_samples_of_one_model(tmp_path: Path) -> None:
    fake = VendorFake()

    result = run(tmp_path, Arm.SELF_MOA, fake)

    samples, chair = fake.rounds
    assert len(samples) == 3 and all(vendor_of(p) is None for p in samples.values())
    (chair_request,) = chair.values()
    assert "3 samples of one model" in chair_request["system"][0]["text"]
    assert result.classification is not None


def test_self_consistency_needs_k() -> None:
    with pytest.raises(ValueError, match="needs k"):
        arms.attempt(Arm.SELF_CONSISTENCY, CONFIG, GUIDE, None)


def spent(billed: int) -> ClassifyResult:
    usage = Usage(
        input_tokens=billed,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return ClassifyResult(
        pair_id="synthetic",
        arm=Arm.SINGLE_MID,
        classification=None,
        errors=[],
        calls=[Call(model="synthetic", usage=usage)],
    )


def test_k_matches_council_ranked_s_billed_tokens_to_one_sample_s() -> None:
    assert arms.matched_k([spent(27_000), spent(29_000)], [spent(3_500)]) == 8


def estimate(arm: Arm, tmp_path: Path, k: int | None = None) -> ArmEstimate:
    return arms.estimate_arm([item()], arm, CONFIG, GUIDE, tmp_path, PRICES, k)


@pytest.mark.parametrize(
    ("arm", "requests"),
    [(Arm.COUNCIL, 4), (Arm.COUNCIL_RANKED, 7), (Arm.SELF_MOA, 4), (Arm.SELF_CONSISTENCY, 5)],
)
def test_the_estimate_prices_every_request_an_arm_makes(
    tmp_path: Path, arm: Arm, requests: int
) -> None:
    found = estimate(arm, tmp_path, k=5)

    assert (found.pairs, found.requests) == (1, requests)
    assert 0 < found.cached_usd < found.typical_usd < found.ceiling_usd


def test_the_estimate_skips_a_pair_the_recordings_finish(tmp_path: Path) -> None:
    run(tmp_path, Arm.COUNCIL, VendorFake())

    assert estimate(Arm.COUNCIL, tmp_path).pairs == 0


def test_the_simulated_client_answers_rankings_with_the_letters_shown() -> None:
    client = Simulated()
    arms.council(item(), CONFIG, GUIDE, client, ranked=True)

    assert len(client.sent) == 7


def test_only_the_council_arms_need_other_vendors() -> None:
    assert arms.vendors(Arm.COUNCIL_RANKED) == {Vendor.OPENAI, Vendor.GOOGLE}
    assert arms.vendors(Arm.SELF_CONSISTENCY) == set()


def test_a_provisional_k_matches_typical_tokens() -> None:
    pairs: list[PairInput] = [item()]

    assert arms.provisional_k(pairs, CONFIG, GUIDE) > 1
