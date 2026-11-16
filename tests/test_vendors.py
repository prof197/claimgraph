"""The other vendors' replies, recordings, retries and batches, all on synthetic responses."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from claimgraph.batch import BatchOutcome
from claimgraph.llm import (
    ModelClient,
    ReplayMiss,
    Reply,
    Usage,
    Vendor,
    parse_reply,
    read_request,
    recording_files,
    request_key,
    retry_request,
    wrap,
)
from claimgraph.vendors import GoogleBatches, OpenAIBatches, VendorBatches, vendor_batches

OPENAI_REQUEST = wrap(
    Vendor.OPENAI,
    {
        "model": "synthetic-openai-model",
        "instructions": "Synthetic instructions.",
        "input": [{"role": "user", "content": "Synthetic pair."}],
        "text": {"format": {"type": "json_schema", "name": "x", "schema": {}, "strict": True}},
        "reasoning": {"effort": "low"},
        "max_output_tokens": 2048,
    },
)
GOOGLE_REQUEST = wrap(
    Vendor.GOOGLE,
    {
        "model": "synthetic-google-model",
        "contents": [{"role": "user", "parts": [{"text": "Synthetic pair."}]}],
        "config": {"system_instruction": "Synthetic instructions.", "max_output_tokens": 2048},
    },
)


def openai_response(text: str, status: str = "completed", **usage: int) -> dict[str, Any]:
    counts = {"input": 1000, "cached": 600, "written": 100, "output": 400, "reasoning": 300}
    counts |= usage
    return {
        "id": "resp_synthetic",
        "model": "synthetic-openai-model",
        "status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ],
        "usage": {
            "input_tokens": counts["input"],
            "input_tokens_details": {
                "cached_tokens": counts["cached"],
                "cache_write_tokens": counts["written"],
            },
            "output_tokens": counts["output"],
            "output_tokens_details": {"reasoning_tokens": counts["reasoning"]},
            "total_tokens": counts["input"] + counts["output"],
        },
    }


def google_response(text: str, finish: str = "STOP") -> dict[str, Any]:
    return {
        "response_id": "synthetic-google-response",
        "model_version": "synthetic-google-model",
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "Synthetic hidden thought.", "thought": True},
                        {"text": text},
                    ],
                },
                "finish_reason": finish,
            }
        ],
        "usage_metadata": {
            "prompt_token_count": 1000,
            "cached_content_token_count": 700,
            "candidates_token_count": 120,
            "thoughts_token_count": 250,
            "total_token_count": 1370,
        },
    }


def test_an_openai_reply_counts_cached_input_apart_and_reasoning_as_output() -> None:
    reply = parse_reply(openai_response('{"a": 1}'), Vendor.OPENAI)

    assert reply.text == '{"a": 1}'
    assert reply.usage == Usage(
        input_tokens=300,
        output_tokens=400,
        cache_creation_input_tokens=100,
        cache_read_input_tokens=600,
        reasoning_tokens=300,
    )
    assert reply.usage.billed == 1400


def test_a_cut_off_openai_reply_says_so() -> None:
    reply = parse_reply(openai_response("{", status="incomplete"), Vendor.OPENAI)

    assert reply.stop_reason == "max_tokens"


def test_a_google_reply_leaves_out_thoughts_and_bills_them_as_output() -> None:
    reply = parse_reply(google_response('{"a": 1}'), Vendor.GOOGLE)

    assert reply.text == '{"a": 1}'
    assert reply.usage == Usage(
        input_tokens=300,
        output_tokens=370,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=700,
        reasoning_tokens=250,
    )
    assert parse_reply(google_response("{", "MAX_TOKENS"), Vendor.GOOGLE).stop_reason == (
        "max_tokens"
    )


@pytest.mark.parametrize("params", [OPENAI_REQUEST, GOOGLE_REQUEST], ids=["openai", "google"])
def test_a_wrapped_request_records_its_shared_parts_once_and_reads_back_whole(
    tmp_path: Path, params: dict[str, Any]
) -> None:
    files = recording_files(params, {"synthetic": True})
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)

    (shared,) = [n for n in files if n.startswith("shared/")]
    assert "Synthetic instructions." in files[shared]
    recorded = json.loads(files[f"{request_key(params)}.json"])
    assert "Synthetic instructions." not in json.dumps(recorded["request"])
    assert read_request(tmp_path, recorded) == params


def test_a_wrapped_recording_replays_through_the_client(tmp_path: Path) -> None:
    client = ModelClient(tmp_path)
    with pytest.raises(ReplayMiss) as miss:
        client.create(GOOGLE_REQUEST)
    assert miss.value.requests == {request_key(GOOGLE_REQUEST): GOOGLE_REQUEST}
    client.record(GOOGLE_REQUEST, google_response('{"a": 1}'))

    assert client.create(GOOGLE_REQUEST).text == '{"a": 1}'


def synthetic_reply(text: str) -> Reply:
    return parse_reply(openai_response(text), Vendor.OPENAI)


def test_an_openai_retry_shows_the_output_back_with_the_note() -> None:
    retry = retry_request(OPENAI_REQUEST, synthetic_reply("{bad"), "Synthetic note.")

    assert [m["role"] for m in retry["body"]["input"]] == ["user", "assistant", "user"]
    assert retry["body"]["input"][1]["content"] == "{bad"
    assert retry["body"]["instructions"] == "Synthetic instructions."


def test_a_google_retry_shows_the_output_back_with_the_note() -> None:
    reply = parse_reply(google_response("{bad"), Vendor.GOOGLE)
    retry = retry_request(GOOGLE_REQUEST, reply, "Synthetic note.")

    contents = retry["body"]["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert contents[1]["parts"] == [{"text": "{bad"}]


def test_a_retry_with_nothing_to_show_asks_again_in_one_turn() -> None:
    retry = retry_request(OPENAI_REQUEST, synthetic_reply(""), "Synthetic note.")

    (only,) = retry["body"]["input"]
    assert only["content"] == "Synthetic pair.\n\nSynthetic note."


# Batches, against stand-ins for the SDK clients


class FakeOpenAI:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.uploaded = b""
        self.outputs = {
            "file-out": "\n".join(
                json.dumps(
                    {
                        "id": "batch_req_1",
                        "custom_id": "key-ok",
                        "response": {"status_code": 200, "body": openai_response("{}")},
                        "error": None,
                    }
                )
                for _ in range(1)
            ),
            "file-err": json.dumps(
                {
                    "id": "batch_req_2",
                    "custom_id": "key-bad",
                    "response": None,
                    "error": {"code": "batch_expired", "message": "Synthetic."},
                }
            ),
        }
        self.files = SimpleNamespace(create=self._upload, content=self._content)
        self.batches = SimpleNamespace(create=self._create, retrieve=self._retrieve)

    def _upload(self, file: tuple[str, bytes], purpose: str) -> Any:
        assert purpose == "batch"
        self.uploaded = file[1]
        return SimpleNamespace(id="file-in")

    def _create(self, input_file_id: str, endpoint: str, completion_window: str) -> Any:
        assert (input_file_id, endpoint, completion_window) == ("file-in", "/v1/responses", "24h")
        return SimpleNamespace(id="batch_synthetic")

    def _retrieve(self, batch_id: str) -> Any:
        status = self.statuses.pop(0)
        return SimpleNamespace(
            status=status,
            request_counts=SimpleNamespace(completed=0, failed=0),
            output_file_id="file-out",
            error_file_id="file-err",
        )

    def _content(self, file_id: str) -> Any:
        return SimpleNamespace(text=self.outputs[file_id])


def test_openai_batches_upload_jsonl_and_sort_results_by_custom_id() -> None:
    fake = FakeOpenAI(["in_progress", "completed"])
    polls: list[str] = []
    api = OpenAIBatches(cast(Any, fake), sleep=lambda _: None, on_poll=polls.append)

    batch_id = api.submit({"key-ok": OPENAI_REQUEST})
    outcome = api.collect(batch_id)

    line = json.loads(fake.uploaded.decode().splitlines()[0])
    assert line == {
        "custom_id": "key-ok",
        "method": "POST",
        "url": "/v1/responses",
        "body": OPENAI_REQUEST["body"],
    }
    assert outcome.responses["key-ok"]["id"] == "resp_synthetic"
    assert outcome.failures == {"key-bad": "errored: batch_expired"}
    assert len(polls) == 1


class FakeGoogle:
    def __init__(self, keyed: bool = True) -> None:
        self.keyed = keyed
        self.src: list[dict[str, Any]] = []
        self.batches = SimpleNamespace(create=self._create, get=self._get)

    def _create(self, model: str, src: list[dict[str, Any]]) -> Any:
        self.model = model
        self.src = src
        return SimpleNamespace(name="batches/synthetic")

    def _get(self, name: str) -> Any:
        dumped = SimpleNamespace(
            model_dump=lambda **_: google_response("{}"),  # pyright: ignore[reportUnknownLambdaType]
        )
        responses = [
            SimpleNamespace(
                metadata=item["metadata"] if self.keyed else None, response=dumped, error=None
            )
            for item in self.src
        ]
        return SimpleNamespace(
            state=SimpleNamespace(value="JOB_STATE_SUCCEEDED"),
            dest=SimpleNamespace(inlined_responses=responses),
        )


def test_google_batches_send_inline_requests_keyed_in_metadata() -> None:
    fake = FakeGoogle()
    api = GoogleBatches(cast(Any, fake), sleep=lambda _: None)

    outcome = api.collect(api.submit({"key-1": GOOGLE_REQUEST}))

    assert fake.model == "synthetic-google-model"
    assert fake.src[0]["metadata"] == {"key": "key-1"}
    assert fake.src[0]["config"] == GOOGLE_REQUEST["body"]["config"]
    assert outcome.responses["key-1"]["response_id"] == "synthetic-google-response"


def test_a_google_response_without_its_key_is_refused() -> None:
    api = GoogleBatches(cast(Any, FakeGoogle(keyed=False)), sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="without its key"):
        api.collect(api.submit({"key-1": GOOGLE_REQUEST}))


class Recorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[list[str]] = []

    def submit(self, requests: Any) -> str:
        self.sent.append(sorted(requests))
        return f"{self.name}-batch"

    def collect(self, batch_id: str) -> BatchOutcome:
        key = self.sent[-1][0]
        return BatchOutcome(responses={key: {"from": self.name}}, failures={})


def test_vendor_batches_split_a_round_by_vendor_and_merge_the_results() -> None:
    apis = {name: Recorder(name) for name in ("anthropic", "openai", "google")}
    api = VendorBatches(cast(Any, apis))
    claude = {"model": "synthetic", "messages": []}

    batch_id = api.submit({"k1": claude, "k2": OPENAI_REQUEST, "k3": GOOGLE_REQUEST})
    outcome = api.collect(batch_id)

    assert batch_id == "anthropic=anthropic-batch;openai=openai-batch;google=google-batch"
    assert [r.sent for r in apis.values()] == [[["k1"]], [["k2"]], [["k3"]]]
    assert outcome.responses == {
        "k1": {"from": "anthropic"},
        "k2": {"from": "openai"},
        "k3": {"from": "google"},
    }


def test_live_council_batches_need_each_vendor_s_key() -> None:
    env = {"ANTHROPIC_API_KEY": "synthetic", "OPENAI_API_KEY": "synthetic"}

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        vendor_batches(env, {Vendor.OPENAI, Vendor.GOOGLE})
