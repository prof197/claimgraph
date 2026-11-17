"""Measured spend per stage and per run, from a question's run manifests and recordings.

Every figure comes from the token counts the API returned, priced by prices.toml; nothing is
typed by hand. Retrieval calls no model, so it costs nothing to run.
"""

import json
import re
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from claimgraph.llm import Usage, parse_reply
from claimgraph.models import Record
from claimgraph.prices import Prices, cost

# The README block that costs --readme writes.
README_BLOCK = "run-costs"

# A first stage of a run, such as corpus-first200-sonnet. Its recordings are the full run's, so
# the full run's cost already counts it.
_FIRST_STAGE = re.compile(r"^(?P<sample>.+)-first\d+-(?P<model>[^-]+)$")


class Stage(StrEnum):
    RETRIEVE = "retrieve"
    EXTRACT = "extract"
    NORMALIZE = "normalize"
    CLASSIFY = "classify"


class RunCost(Record):
    stage: Stage
    run: str
    models: list[str]
    requests: int
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_usd: float
    # The price file's date the cost was worked out with; None when no model was called.
    prices_checked: date | None
    # Another run whose recordings hold this one's, so the stage total leaves this one out.
    counted_in: str | None = None


class _Lenient(BaseModel):
    # Manifests and results written before a field was added still read.
    model_config = ConfigDict(extra="ignore")


class _ExtractEntry(_Lenient):
    model: str
    requests: int
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_usd: float
    prices_checked: date


class _Retrieved(_Lenient):
    requests: int


class _RunManifest(_Lenient):
    retrieve: _Retrieved


class _Call(_Lenient):
    model: str
    usage: Usage


class _Classified(_Lenient):
    calls: list[_Call] = []
    # Results written before arms could call several models name one model for every request.
    model: str | None = None
    usage: list[Usage] = []

    def priced(self) -> list[tuple[str, Usage]]:
        if self.model is not None:
            return [(self.model, u) for u in self.usage]
        return [(c.model, c.usage) for c in self.calls]


def _run_cost(
    stage: Stage, run: str, calls: Sequence[tuple[str, Usage]], prices: Prices, *, batch: bool
) -> RunCost:
    return RunCost(
        stage=stage,
        run=run,
        models=sorted({model for model, _ in calls}),
        requests=len(calls),
        input_tokens=sum(u.input_tokens for _, u in calls),
        cache_write_tokens=sum(u.cache_creation_input_tokens for _, u in calls),
        cache_read_tokens=sum(u.cache_read_input_tokens for _, u in calls),
        output_tokens=sum(u.output_tokens for _, u in calls),
        cost_usd=sum(cost(u, model, prices, batch=batch) for model, u in calls),
        prices_checked=prices.checked,
    )


def retrieve_cost(run_dir: Path) -> RunCost | None:
    """The last retrieval: its E-utilities requests, which are free."""
    path = run_dir / "manifest.json"
    if not path.exists():
        return None
    retrieved = _RunManifest.model_validate_json(path.read_text()).retrieve
    return RunCost(
        stage=Stage.RETRIEVE,
        run="last retrieval",
        models=[],
        requests=retrieved.requests,
        input_tokens=0,
        cache_write_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        prices_checked=None,
    )


def extract_costs(run_dir: Path) -> list[RunCost]:
    """Each entry of the extract stage's manifest, with the cost the run recorded at batch
    prices, so a later price change does not rewrite what was spent."""
    path = run_dir / "extract" / "manifest.json"
    if not path.exists():
        return []
    runs = cast(dict[str, Any], json.loads(path.read_text())["runs"])
    costs: list[RunCost] = []
    for name in sorted(runs):
        entry = _ExtractEntry.model_validate(runs[name])
        stage = _FIRST_STAGE.match(name)
        full = f"{stage['sample']}-{stage['model']}" if stage else None
        costs.append(
            RunCost(
                stage=Stage.EXTRACT,
                run=name,
                models=[entry.model],
                requests=entry.requests,
                input_tokens=entry.input_tokens,
                cache_write_tokens=entry.cache_write_tokens,
                cache_read_tokens=entry.cache_read_tokens,
                output_tokens=entry.output_tokens,
                cost_usd=entry.cost_usd,
                prices_checked=entry.prices_checked,
                counted_in=full if full in runs else None,
            )
        )
    return costs


def normalize_costs(replay_dir: Path, prices: Prices) -> list[RunCost]:
    """Each recorded outcome proposal, at standard prices: the proposal is one direct request.

    No manifest records the proposal's tokens, so they are read from its recordings, one
    directory per extraction model, each file named by its request's key.
    """
    if not replay_dir.exists():
        return []
    costs: list[RunCost] = []
    for model_dir in sorted(p for p in replay_dir.iterdir() if p.is_dir()):
        for path in sorted(model_dir.glob("*.json")):
            reply = parse_reply(json.loads(path.read_text())["response"])
            run = f"{model_dir.name} proposal {path.stem[:8]}"
            costs.append(
                _run_cost(Stage.NORMALIZE, run, [(reply.model, reply.usage)], prices, batch=False)
            )
    return costs


def classify_costs(run_dir: Path, prices: Prices) -> list[RunCost]:
    """Each classification run, one results file per split and arm, at batch prices.

    The results keep each request's tokens but not its cost, so they are priced with the
    current price file.
    """
    out = run_dir / "classify"
    if not out.exists():
        return []
    costs: list[RunCost] = []
    for path in sorted(out.glob("*.jsonl")):
        results = [_Classified.model_validate_json(line) for line in path.read_text().splitlines()]
        calls = [call for r in results for call in r.priced()]
        costs.append(_run_cost(Stage.CLASSIFY, path.stem, calls, prices, batch=True))
    return costs


def run_costs(run_dir: Path, normalization_replay: Path, prices: Prices) -> list[RunCost]:
    """Every recorded run of a question, stage by stage."""
    retrieved = retrieve_cost(run_dir)
    return [
        *([retrieved] if retrieved else []),
        *extract_costs(run_dir),
        *normalize_costs(normalization_replay, prices),
        *classify_costs(run_dir, prices),
    ]


def _usd(value: float) -> str:
    return f"${value:.4f}"


def table(costs: Sequence[RunCost]) -> str:
    """The README block: every run with its tokens and cost, then the total per stage."""
    lines = [
        "Measured spend of each recorded run, from the token counts the API returned and the "
        "price file of the date shown. Extraction and classification go through batches and "
        "are priced at batch rates; the outcome proposal is one direct request, priced at "
        "standard rates. Retrieval calls no model. A run counts every response it recorded, "
        "replayed ones included.",
        "",
        "| Stage | Run | Models | Requests | Input | Cache writes | Cache reads | Output | Cost "
        "| Prices of |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for c in costs:
        models = ", ".join(c.models) if c.models else "PubMed E-utilities"
        checked = str(c.prices_checked) if c.prices_checked else "free"
        lines.append(
            f"| {c.stage.value} | {c.run} | {models} | {c.requests:,} | {c.input_tokens:,} | "
            f"{c.cache_write_tokens:,} | {c.cache_read_tokens:,} | {c.output_tokens:,} | "
            f"{_usd(c.cost_usd)} | {checked} |"
        )
    lines += ["", "| Stage | Runs | Cost |", "| --- | ---: | ---: |"]
    total = 0.0
    for stage in Stage:
        runs = [c for c in costs if c.stage is stage]
        if not runs:
            continue
        spent = sum(c.cost_usd for c in runs if c.counted_in is None)
        total += spent
        lines.append(f"| {stage.value} | {len(runs)} | {_usd(spent)} |")
    lines.append(f"| All stages | {len(costs)} | {_usd(total)} |")
    shared = [c for c in costs if c.counted_in is not None]
    if shared:
        lines += [
            "",
            " ".join(
                f"{c.run}'s recordings are {c.counted_in}'s, so the totals count them once."
                for c in shared
            ),
        ]
    return "\n".join(lines) + "\n"
