"""The model client: replay by default, live only on request, every response recorded."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from claimgraph import llm
from claimgraph.llm import Mode, ModelClient, ReplayMiss, parse_reply, request_key

FIXTURES = Path(__file__).parent / "fixtures" / "replay"

PARAMS: dict[str, Any] = {
    "model": "synthetic-model",
    "max_tokens": 256,
    "system": "Synthetic instructions.",
    "messages": [{"role": "user", "content": "Synthetic abstract text."}],
}


def synthetic_response(text: str = '{"synthetic": true}') -> dict[str, Any]:
    return {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": "synthetic-model",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 40,
            "output_tokens": 12,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": 30,
        },
    }


class FakeTransport:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.response = response or synthetic_response()

    def __call__(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        return self.response


def never_connect(env: Mapping[str, str]) -> llm.Transport:
    raise AssertionError("replay must not build a live client")


def test_the_key_depends_on_every_parameter_and_not_on_key_order() -> None:
    reordered = dict(reversed(PARAMS.items()))
    other_text = PARAMS | {"messages": [{"role": "user", "content": "Other synthetic text."}]}

    assert request_key(reordered) == request_key(PARAMS)
    assert request_key(PARAMS | {"model": "other-synthetic-model"}) != request_key(PARAMS)
    assert request_key(PARAMS | {"max_tokens": 257}) != request_key(PARAMS)
    assert request_key(other_text) != request_key(PARAMS)


def test_replay_reads_a_recorded_response_with_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "anthropic_transport", never_connect)

    reply = ModelClient(FIXTURES).create(PARAMS)

    assert reply.text == '{"synthetic": true}'
    assert reply.stop_reason == "end_turn"
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (40, 12)
    assert (reply.usage.cache_creation_input_tokens, reply.usage.cache_read_input_tokens) == (0, 30)


def test_a_missing_response_fails_in_replay_rather_than_calling_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "anthropic_transport", never_connect)

    with pytest.raises(ReplayMiss, match=request_key(PARAMS)[:12]):
        ModelClient(tmp_path).create(PARAMS)
    assert list(tmp_path.iterdir()) == []


def test_live_records_each_new_response_once(tmp_path: Path) -> None:
    transport = FakeTransport()
    client = ModelClient(tmp_path, Mode.LIVE, transport=transport)

    first = client.create(PARAMS)
    second = client.create(dict(reversed(PARAMS.items())))

    # The rerun is served from the recording, so it costs nothing.
    assert len(transport.calls) == 1
    assert first == second
    (path,) = tmp_path.glob("*.json")
    assert path.name == f"{request_key(PARAMS)}.json"
    recorded = json.loads(path.read_text())
    assert recorded["response"] == synthetic_response()
    assert ModelClient(tmp_path).create(PARAMS) == first


def test_the_shared_instructions_are_stored_once(tmp_path: Path) -> None:
    client = ModelClient(tmp_path, Mode.LIVE, transport=FakeTransport())
    other = PARAMS | {"messages": [{"role": "user", "content": "Other synthetic abstract."}]}

    client.create(PARAMS)
    client.create(other)

    (shared,) = (tmp_path / "shared").glob("*.json")
    assert json.loads(shared.read_text()) == {"system": PARAMS["system"]}
    for path in tmp_path.glob("*.json"):
        recorded = json.loads(path.read_text())
        assert "system" not in recorded["request"]
        assert recorded["request"]["shared"] == shared.stem
        assert PARAMS["system"] not in path.read_text()
    assert ModelClient(tmp_path).create(other).text == '{"synthetic": true}'


def test_a_changed_shared_file_no_longer_matches(tmp_path: Path) -> None:
    ModelClient(tmp_path, Mode.LIVE, transport=FakeTransport()).create(PARAMS)
    (shared,) = (tmp_path / "shared").glob("*.json")
    shared.write_text(json.dumps({"system": "Edited synthetic instructions."}))

    with pytest.raises(ValueError, match="does not match"):
        ModelClient(tmp_path).create(PARAMS)


def test_a_recording_that_does_not_match_its_request_is_rejected(tmp_path: Path) -> None:
    ModelClient(tmp_path, Mode.LIVE, transport=FakeTransport()).create(PARAMS)
    (path,) = tmp_path.glob("*.json")
    recorded = json.loads(path.read_text())
    recorded["request"]["max_tokens"] = 999
    path.write_text(json.dumps(recorded))

    with pytest.raises(ValueError, match="does not match"):
        ModelClient(tmp_path).create(PARAMS)


def test_live_needs_an_api_key(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.anthropic_transport({})


def test_the_reply_keeps_text_and_skips_other_blocks() -> None:
    raw = synthetic_response() | {
        "content": [
            {"type": "thinking", "thinking": "Synthetic.", "signature": "synthetic"},
            {"type": "text", "text": '{"synthetic": '},
            {"type": "text", "text": "true}"},
        ],
        "stop_reason": "max_tokens",
    }

    reply = parse_reply(raw)

    assert reply.text == '{"synthetic": true}'
    assert reply.stop_reason == "max_tokens"


def test_the_recorded_fixture_is_current() -> None:
    # The fixture is what the client writes; if the format changes, re-record it on purpose.
    expected = llm.recording_files(PARAMS, synthetic_response())
    found = {
        p.relative_to(FIXTURES).as_posix(): p.read_text()
        for p in FIXTURES.rglob("*")
        if p.is_file()
    }

    assert found == expected
    assert f"{request_key(PARAMS)}.json" in found


def test_the_client_knows_which_recordings_it_used(tmp_path: Path) -> None:
    other = PARAMS | {"max_tokens": 128}
    ModelClient(tmp_path, Mode.LIVE, transport=FakeTransport()).create(PARAMS)

    client = ModelClient(tmp_path)
    client.create(PARAMS)
    with pytest.raises(ReplayMiss):
        client.create(other)

    assert client.used == {request_key(PARAMS)}
