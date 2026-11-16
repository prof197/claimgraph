"""The model client. Every request goes through a replay cache keyed by the request itself.

Replay is the default: a recorded response is returned without touching the network, and a
missing one is an error rather than a call, so CI and reruns cost nothing. Current models accept
no sampling parameters, so the recording, not a temperature setting, is what makes a rerun give
the same answer. Live mode reads the recordings first and records each new response, so a live
rerun never pays twice for the same request.

A Claude request is the Messages API's own parameters. A request to another vendor, which only
the council arms send, is wrapped as {"vendor": ..., "body": ...}, with the body in that vendor's
own format; its reply is read into the same Reply.
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


class Vendor(StrEnum):
    OPENAI = "openai"
    GOOGLE = "google"


def vendor_of(params: Mapping[str, Any]) -> Vendor | None:
    """The vendor of a wrapped request; None for a Claude request."""
    return Vendor(params["vendor"]) if "vendor" in params else None


def wrap(vendor: Vendor, body: Mapping[str, Any]) -> dict[str, Any]:
    return {"vendor": vendor.value, "body": dict(body)}


class ReplayMiss(LookupError):
    """No recording for one or more requests; carries them so a batch can send them."""

    def __init__(self, key: str, params: Mapping[str, Any], replay_dir: Path) -> None:
        super().__init__(
            f"no recorded response for request {key[:12]} in {replay_dir}; record it with --live"
        )
        self.key = key
        self.params = params
        # Every request missing at once, by key: a step that sends several in parallel, such
        # as a council's members, reports them together so one batch carries them all.
        self.requests: dict[str, Mapping[str, Any]] = {key: params}


def gather(misses: list[ReplayMiss]) -> ReplayMiss:
    """One miss carrying the requests of several."""
    first, *rest = misses
    for miss in rest:
        first.requests |= miss.requests
    return first


class Usage(Record):
    # Uncached input; cache reads and writes are counted apart, as the Messages API counts them.
    input_tokens: int
    # Everything billed as output, hidden reasoning included.
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    # The share of output_tokens the model reasoned with and did not show.
    reasoning_tokens: int = 0

    @property
    def billed(self) -> int:
        """Every token the request was billed for, at whatever rate."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


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


def parse_reply(raw: Mapping[str, Any], vendor: Vendor | None = None) -> Reply:
    if vendor is Vendor.OPENAI:
        return _parse_openai(raw)
    if vendor is Vendor.GOOGLE:
        return _parse_google(raw)
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


class _OpenAIContent(_Lenient):
    type: str
    text: str | None = None


class _OpenAIItem(_Lenient):
    type: str
    content: list[_OpenAIContent] | None = None


class _OpenAIInputDetails(_Lenient):
    cached_tokens: int = 0
    cache_write_tokens: int = 0


class _OpenAIOutputDetails(_Lenient):
    reasoning_tokens: int = 0


class _OpenAIUsage(_Lenient):
    input_tokens: int
    output_tokens: int
    input_tokens_details: _OpenAIInputDetails | None = None
    output_tokens_details: _OpenAIOutputDetails | None = None


class _OpenAIIncomplete(_Lenient):
    reason: str | None = None


class _OpenAIResponse(_Lenient):
    id: str
    model: str
    status: str | None = None
    incomplete_details: _OpenAIIncomplete | None = None
    output: list[_OpenAIItem]
    usage: _OpenAIUsage


def _parse_openai(raw: Mapping[str, Any]) -> Reply:
    """A Responses API response. Its input_tokens include the cached reads and writes, and its
    output_tokens the hidden reasoning, which is billed as output."""
    r = _OpenAIResponse.model_validate(raw)
    details = r.usage.input_tokens_details or _OpenAIInputDetails()
    reasoning = (r.usage.output_tokens_details or _OpenAIOutputDetails()).reasoning_tokens
    cut = r.incomplete_details is not None and r.incomplete_details.reason == "max_output_tokens"
    texts = [
        c.text or ""
        for item in r.output
        if item.type == "message"
        for c in item.content or []
        if c.type == "output_text"
    ]
    return Reply(
        id=r.id,
        model=r.model,
        stop_reason="max_tokens" if cut else r.status,
        text="".join(texts),
        usage=Usage(
            input_tokens=r.usage.input_tokens - details.cached_tokens - details.cache_write_tokens,
            output_tokens=r.usage.output_tokens,
            cache_creation_input_tokens=details.cache_write_tokens,
            cache_read_input_tokens=details.cached_tokens,
            reasoning_tokens=reasoning,
        ),
    )


class _GooglePart(_Lenient):
    text: str | None = None
    thought: bool | None = None


class _GoogleContent(_Lenient):
    parts: list[_GooglePart] | None = None


class _GoogleCandidate(_Lenient):
    content: _GoogleContent | None = None
    finish_reason: str | None = None


class _GoogleUsage(_Lenient):
    prompt_token_count: int = 0
    candidates_token_count: int = 0
    thoughts_token_count: int = 0
    cached_content_token_count: int = 0


class _GoogleResponse(_Lenient):
    response_id: str | None = None
    model_version: str | None = None
    candidates: list[_GoogleCandidate] | None = None
    usage_metadata: _GoogleUsage | None = None


def _parse_google(raw: Mapping[str, Any]) -> Reply:
    """A generateContent response, as the SDK dumps it. Its prompt count includes the cached
    tokens; thinking is counted apart from the answer and billed as output with it."""
    r = _GoogleResponse.model_validate(raw)
    usage = r.usage_metadata or _GoogleUsage()
    first = r.candidates[0] if r.candidates else _GoogleCandidate()
    parts = first.content.parts if first.content and first.content.parts else []
    return Reply(
        id=r.response_id or "",
        model=r.model_version or "",
        stop_reason="max_tokens" if first.finish_reason == "MAX_TOKENS" else first.finish_reason,
        text="".join(p.text or "" for p in parts if not p.thought),
        usage=Usage(
            input_tokens=usage.prompt_token_count - usage.cached_content_token_count,
            output_tokens=usage.candidates_token_count + usage.thoughts_token_count,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=usage.cached_content_token_count,
            reasoning_tokens=usage.thoughts_token_count,
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
# The same parts of another vendor's request body: instructions, schema and settings.
VENDOR_SHARED_FIELDS: dict[Vendor, tuple[str, ...]] = {
    Vendor.OPENAI: ("instructions", "text", "reasoning"),
    Vendor.GOOGLE: ("config",),
}
SHARED_DIR = "shared"


def _split_shared(params: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A request's shared parts, and the rest."""
    vendor = vendor_of(params)
    if vendor is None:
        shared = {k: params[k] for k in SHARED_FIELDS if k in params}
        return shared, {k: v for k, v in params.items() if k not in SHARED_FIELDS}
    body = params["body"]
    fields = VENDOR_SHARED_FIELDS[vendor]
    shared = {k: body[k] for k in fields if k in body}
    rest = {k: v for k, v in body.items() if k not in fields}
    return ({"body": shared} if shared else {}), {**params, "body": rest}


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def recording_files(params: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, str]:
    """The files that record one response, by path in the replay directory, with their text.

    The recording holds the request beside its response, so either can be checked; the shared
    instructions and schema go in a file of their own, named by their hash.
    """
    shared, request = _split_shared(params)
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
        shared = json.loads((replay_dir / SHARED_DIR / f"{shared_id}.json").read_text())
        if "body" in shared:
            request["body"] = {**request["body"], **shared.pop("body")}
        request |= shared
    return request


def recorded_request(replay_dir: Path, key: str) -> dict[str, Any]:
    """The full request of the recording with this key; FileNotFoundError if there is none."""
    return read_request(replay_dir, json.loads((replay_dir / f"{key}.json").read_text()))


def retry_request(params: Mapping[str, Any], reply: Reply, note: str) -> dict[str, Any]:
    """The first request again, with the output it got and a note on what to fix."""
    vendor = vendor_of(params)
    if vendor is Vendor.OPENAI:
        body = params["body"]
        (first, *_) = body["input"]
        turns = [first, {"role": "assistant", "content": reply.text}] if reply.text else []
        if not turns:
            return wrap(vendor, {**body, "input": [_joined(first, note)]})
        return wrap(vendor, {**body, "input": [*turns, {"role": "user", "content": note}]})
    if vendor is Vendor.GOOGLE:
        body = params["body"]
        (first, *_) = body["contents"]
        if not reply.text:
            text = f"{first['parts'][0]['text']}\n\n{note}"
            return wrap(vendor, {**body, "contents": [{"role": "user", "parts": [{"text": text}]}]})
        contents = [
            first,
            {"role": "model", "parts": [{"text": reply.text}]},
            {"role": "user", "parts": [{"text": note}]},
        ]
        return wrap(vendor, {**body, "contents": contents})
    (first, *_) = params["messages"]
    if reply.text:
        messages = [
            first,
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": note},
        ]
    else:
        # Nothing to show back, such as an empty refusal: ask again in the one user turn.
        messages = [{"role": "user", "content": f"{first['content']}\n\n{note}"}]
    return {**params, "messages": messages}


def _joined(first: Mapping[str, Any], note: str) -> dict[str, Any]:
    return {"role": "user", "content": f"{first['content']}\n\n{note}"}


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
            return parse_reply(recorded["response"], vendor_of(params))
        if self.mode is Mode.REPLAY:
            raise ReplayMiss(key, params, self.replay_dir)
        if vendor_of(params) is not None:
            # Other vendors' requests are sent in batches only.
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
