"""Batches for the council's other vendors, beside Claude's, behind one batch interface.

Only the council arms send to OpenAI and Google, so their SDKs are an optional dependency,
imported when a live run builds its batch clients and never in replay. A round's requests are
split by vendor and each part goes to that vendor's batch API; every response is recorded raw,
in the vendor's own format, and read back into a Reply by the client.
"""

import json
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from claimgraph.batch import (
    POLL_SECONDS,
    BatchApi,
    BatchOutcome,
    PartialSubmit,
    anthropic_batches,
)
from claimgraph.llm import Vendor, vendor_of

if TYPE_CHECKING:
    from google.genai import Client as GoogleClient
    from openai import OpenAI

# The key that names Claude's part of a combined batch id.
CLAUDE = "anthropic"
ENV_KEYS = {Vendor.OPENAI: "OPENAI_API_KEY", Vendor.GOOGLE: "GEMINI_API_KEY"}

_OPENAI_DONE = {"completed", "failed", "expired", "cancelled"}
_GOOGLE_DONE = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}
# The SDK's own extras on a response, which are not the API's answer.
_GOOGLE_EXTRAS = {"sdk_http_response", "automatic_function_calling_history", "parsed"}


class OpenAIBatches:
    """The Batch API over /v1/responses: a JSONL file in, one out, matched by custom_id."""

    def __init__(
        self,
        client: "OpenAI",
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = POLL_SECONDS,
        on_poll: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.client = client
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.on_poll = on_poll

    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        lines = [
            json.dumps(
                {"custom_id": key, "method": "POST", "url": "/v1/responses", "body": p["body"]},
                ensure_ascii=False,
            )
            for key, p in requests.items()
        ]
        data = ("\n".join(lines) + "\n").encode()
        uploaded = self.client.files.create(file=("requests.jsonl", data), purpose="batch")
        batch = self.client.batches.create(
            input_file_id=uploaded.id, endpoint="/v1/responses", completion_window="24h"
        )
        return batch.id

    def collect(self, batch_id: str) -> BatchOutcome:
        while True:
            batch = self.client.batches.retrieve(batch_id)
            if batch.status in _OPENAI_DONE:
                break
            counts = batch.request_counts
            done = f"{counts.completed} completed, {counts.failed} failed" if counts else ""
            self.on_poll(f"batch {batch_id}: {batch.status} {done}".rstrip())
            self.sleep(self.poll_seconds)
        responses: dict[str, dict[str, Any]] = {}
        failures: dict[str, str] = {}
        # Output lines may come in any order; custom_id matches them to requests.
        for file_id in (batch.output_file_id, batch.error_file_id):
            if file_id is None:
                continue
            for line in self.client.files.content(file_id).text.splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                response = entry.get("response")
                if response is not None and response.get("status_code") == 200:
                    responses[entry["custom_id"]] = response["body"]
                else:
                    error = cast(dict[str, Any], entry.get("error") or {})
                    failures[entry["custom_id"]] = f"errored: {error.get('code', 'unknown')}"
        if batch.status != "completed" and not responses and not failures:
            self.on_poll(f"batch {batch_id} ended {batch.status} with no results")
        return BatchOutcome(responses=responses, failures=failures)


class GoogleBatches:
    """The Gemini Batch API with inline requests, each carrying its key in metadata."""

    def __init__(
        self,
        client: "GoogleClient",
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = POLL_SECONDS,
        on_poll: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.client = client
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.on_poll = on_poll

    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        models = {p["body"]["model"] for p in requests.values()}
        if len(models) != 1:
            raise ValueError(f"one Gemini batch takes one model, not {sorted(models)}")
        src: Any = [
            {
                "contents": p["body"]["contents"],
                "config": p["body"]["config"],
                "metadata": {"key": k},
            }
            for k, p in requests.items()
        ]
        job = self.client.batches.create(model=models.pop(), src=src)
        if job.name is None:
            raise RuntimeError("the Gemini batch was created without a name")
        return job.name

    def collect(self, batch_id: str) -> BatchOutcome:
        while True:
            job = self.client.batches.get(name=batch_id)
            state = job.state.value if job.state is not None else "unknown"
            if state in _GOOGLE_DONE:
                break
            self.on_poll(f"batch {batch_id}: {state}")
            self.sleep(self.poll_seconds)
        responses: dict[str, dict[str, Any]] = {}
        failures: dict[str, str] = {}
        inlined = job.dest.inlined_responses if job.dest is not None else None
        for item in inlined or []:
            key = (item.metadata or {}).get("key")
            if key is None:
                raise RuntimeError(f"batch {batch_id} returned a response without its key")
            if item.response is not None and item.error is None:
                responses[key] = item.response.model_dump(
                    mode="json", exclude_none=True, exclude=_GOOGLE_EXTRAS
                )
            else:
                code = item.error.code if item.error is not None else "no response"
                failures[key] = f"errored: {code}"
        if state != "JOB_STATE_SUCCEEDED":
            self.on_poll(f"batch {batch_id} ended {state}")
        return BatchOutcome(responses=responses, failures=failures)


class VendorBatches:
    """Several vendors' batch APIs as one: a round's requests are split by vendor, and the
    combined id names each part, so a stopped run can collect them all."""

    def __init__(self, apis: Mapping[str, BatchApi]) -> None:
        self.apis = apis

    def submit(self, requests: Mapping[str, Mapping[str, Any]]) -> str:
        parts: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
        for key, params in requests.items():
            vendor = vendor_of(params)
            parts[CLAUDE if vendor is None else vendor.value][key] = params
        missing = sorted(set(parts) - set(self.apis))
        if missing:
            raise RuntimeError(f"no batch client for {', '.join(missing)}")
        sent: list[str] = []
        for name, part in parts.items():
            try:
                sent.append(f"{name}={self.apis[name].submit(part)}")
            except Exception as error:
                if sent:
                    # The parts already sent are paid for; the run records them as pending.
                    raise PartialSubmit(";".join(sent), error) from error
                raise
        return ";".join(sent)

    def collect(self, batch_id: str) -> BatchOutcome:
        responses: dict[str, dict[str, Any]] = {}
        failures: dict[str, str] = {}
        for part in batch_id.split(";"):
            name, _, inner = part.partition("=")
            outcome = self.apis[name].collect(inner)
            responses |= outcome.responses
            failures |= outcome.failures
        return BatchOutcome(responses=responses, failures=failures)


def vendor_batches(
    env: Mapping[str, str], vendors: set[Vendor], on_poll: Callable[[str], None] = lambda _: None
) -> VendorBatches:
    """Claude's batches and those of each vendor named, with keys from the environment."""
    for vendor in sorted(vendors):
        if not env.get(ENV_KEYS[vendor]):
            raise RuntimeError(
                f"{ENV_KEYS[vendor]} is not set; the council's live runs need it (see .env.example)"
            )
    apis: dict[str, BatchApi] = {CLAUDE: anthropic_batches(env, on_poll)}
    if Vendor.OPENAI in vendors:
        from openai import OpenAI

        apis[Vendor.OPENAI.value] = OpenAIBatches(
            OpenAI(api_key=env[ENV_KEYS[Vendor.OPENAI]]), on_poll=on_poll
        )
    if Vendor.GOOGLE in vendors:
        from google.genai import Client

        client = Client(api_key=env[ENV_KEYS[Vendor.GOOGLE]])
        apis[Vendor.GOOGLE.value] = GoogleBatches(client, on_poll=on_poll)
    return VendorBatches(apis)
