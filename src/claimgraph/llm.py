"""The model client. Every request goes through a replay cache keyed by the request itself.

Replay is the default: a recorded response is returned without touching the network, and a
missing one is an error rather than a call, so CI and reruns cost nothing. Current models accept
no sampling parameters, so the recording, not a temperature setting, is what makes a rerun give
the same answer. Live mode reads the recordings first and records each new response, so a live
rerun never pays twice for the same request.
"""

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import anthropic
from pydantic import BaseModel, ConfigDict

from claimgraph.models import Record
from claimgraph.retrieve import write_atomic

# Sends one Messages API request and returns the response as JSON.
type Transport = Callable[[Mapping[str, Any]], dict[str, Any]]


class Mode(StrEnum):
    REPLAY = "replay"
    LIVE = "live"


class ReplayMiss(LookupError):
    """No recording for a request; carries the request so a batch can send it."""

    def __init__(self, key: str, params: Mapping[str, Any], replay_dir: Path) -> None:
        super().__init__(
            f"no recorded response for request {key[:12]} in {replay_dir}; record it with --live"
        )
        self.key = key
        self.params = params


class Usage(Record):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class Reply(Record):
    id: str
    model: str
    stop_reason: str | None
    # The text blocks joined; thinking and other blocks are left out.
    text: str
    usage: Usage


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Block(_Lenient):
    type: str
    text: str | None = None


class _Usage(_Lenient):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None


class _Message(_Lenient):
    id: str
    model: str
    stop_reason: str | None
    content: list[_Block]
    usage: _Usage


def parse_reply(raw: Mapping[str, Any]) -> Reply:
    message = _Message.model_validate(raw)
    usage = message.usage
    return Reply(
        id=message.id,
        model=message.model,
        stop_reason=message.stop_reason,
        text="".join(b.text or "" for b in message.content if b.type == "text"),
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        ),
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_key(params: Mapping[str, Any]) -> str:
    """A hash of the whole request: model, parameters and messages."""
    return hashlib.sha256(_canonical(params).encode()).hexdigest()


# The parts of a request every abstract shares: the instructions and the response schema. A
# recording names them by hash, and they are stored once per prompt version under shared/.
SHARED_FIELDS = ("system", "output_config")
SHARED_DIR = "shared"


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def recording_files(params: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, str]:
    """The files that record one response, by path in the replay directory, with their text.

    The recording holds the request beside its response, so either can be checked; the shared
    instructions and schema go in a file of their own, named by their hash.
    """
    shared = {k: params[k] for k in SHARED_FIELDS if k in params}
    request = {k: v for k, v in params.items() if k not in SHARED_FIELDS}
    files: dict[str, str] = {}
    if shared:
        shared_id = hashlib.sha256(_canonical(shared).encode()).hexdigest()
        files[f"{SHARED_DIR}/{shared_id}.json"] = _dumps(shared)
        request["shared"] = shared_id
    key = request_key(params)
    files[f"{key}.json"] = _dumps({"key": key, "request": request, "response": response})
    return files


def read_request(replay_dir: Path, recorded: Mapping[str, Any]) -> dict[str, Any]:
    """A recording's full request, with its shared instructions and schema put back."""
    request = dict(recorded["request"])
    shared_id = request.pop("shared", None)
    if shared_id is not None:
        request |= json.loads((replay_dir / SHARED_DIR / f"{shared_id}.json").read_text())
    return request


def anthropic_transport(env: Mapping[str, str]) -> Transport:
    if not env.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set; live runs need it (see .env.example)")
    client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])

    def send(params: Mapping[str, Any]) -> dict[str, Any]:
        # Never streamed, so the call returns one whole message.
        message = cast(anthropic.types.Message, client.messages.create(**params))
        return message.model_dump(mode="json")

    return send


class ModelClient:
    def __init__(
        self, replay_dir: Path, mode: Mode = Mode.REPLAY, transport: Transport | None = None
    ) -> None:
        self.replay_dir = replay_dir
        self.mode = mode
        self._transport = transport
        # Keys of the recordings this client served or made, so unused ones can be found.
        self.used: set[str] = set()

    def create(self, params: Mapping[str, Any]) -> Reply:
        key = request_key(params)
        path = self.replay_dir / f"{key}.json"
        if path.exists() or self.mode is Mode.LIVE:
            self.used.add(key)
        if path.exists():
            recorded = json.loads(path.read_text())
            if request_key(read_request(self.replay_dir, recorded)) != key:
                raise ValueError(f"{path} does not match the request it is named for")
            return parse_reply(recorded["response"])
        if self.mode is Mode.REPLAY:
            raise ReplayMiss(key, params, self.replay_dir)
        if self._transport is None:
            # Built only when a request is actually sent, so replay never needs a key.
            self._transport = anthropic_transport(os.environ)
        response = self._transport(params)
        self.record(params, response)
        return parse_reply(response)

    def record(self, params: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        """Save a response however it was obtained, directly or through a batch."""
        for name, text in recording_files(params, response).items():
            path = self.replay_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(path, text.encode())
