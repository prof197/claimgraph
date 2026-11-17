"""The cost table: measured spend per stage and per run, from synthetic manifests."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from claimgraph.costs import Stage, run_costs, table
from claimgraph.prices import ModelPrices, Prices

# Round synthetic prices, per million tokens, so every cost below can be worked by hand.
PRICES = Prices(
    checked=date(2026, 1, 2),
    batch_discount=0.5,
    max_prompt_tokens=100_000,
    models={
        "synthetic-small": ModelPrices(input=1, output=10, cache_write=2, cache_read=0.5),
        "synthetic-peer": ModelPrices(input=2, output=20, cache_write=2, cache_read=1),
    },
)


def tokens(input: int = 0, output: int = 0, write: int = 0, read: int = 0) -> dict[str, int]:
    return {
        "input_tokens": input,
        "output_tokens": output,
        "cache_creation_input_tokens": write,
        "cache_read_input_tokens": read,
    }


def extract_entry(model: str, requests: int, cost_usd: float, **counts: int) -> dict[str, Any]:
    # A manifest entry from before a count was added still reads, so fields are left out.
    return {
        "model": model,
        "requests": requests,
        "input_tokens": counts.get("input", 0),
        "cache_write_tokens": counts.get("write", 0),
        "cache_read_tokens": counts.get("read", 0),
        "output_tokens": counts.get("output", 0),
        "cost_usd": cost_usd,
        "prices_checked": "2026-01-01",
        "findings": 0,
    }


def synthetic_run(root: Path) -> tuple[Path, Path]:
    run_dir, replay = root / "runs" / "synthetic", root / "replay" / "normalization"
    (run_dir / "extract").mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"retrieve": {"requests": 12}}))
    (run_dir / "extract" / "manifest.json").write_text(
        json.dumps(
            {
                "runs": {
                    "corpus-small": extract_entry("synthetic-small", 4, 2.5, input=1_000_000),
                    "corpus-first2-small": extract_entry("synthetic-small", 2, 1.25, input=500),
                    "dev-small": extract_entry("synthetic-small", 3, 0.5, output=7),
                }
            }
        )
    )
    (replay / "small" / "shared").mkdir(parents=True)
    (replay / "small" / "shared" / "ffff.json").write_text('{"system": "synthetic"}')
    response = {
        "id": "msg_synthetic",
        "model": "synthetic-small",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "{}"}],
        "usage": tokens(input=1_000_000, output=100_000),
    }
    (replay / "small" / "abcdef0123456789.json").write_text(
        json.dumps({"key": "abcdef0123456789", "request": {}, "response": response})
    )
    (run_dir / "classify").mkdir()
    calls = [
        {"model": "synthetic-small", "usage": tokens(input=1_000_000)},
        {"model": "synthetic-peer", "usage": tokens(output=100_000, read=1_000_000)},
    ]
    (run_dir / "classify" / "eval-council.jsonl").write_text(
        json.dumps({"pair_id": "synthetic", "calls": calls}) + "\n"
    )
    # Results from before arms could call several models: one model, a list of usages.
    old = {"pair_id": "synthetic", "model": "synthetic-small", "usage": [tokens(output=100_000)]}
    (run_dir / "classify" / "dev-single-small.round1.jsonl").write_text(
        json.dumps(old) + "\n" + json.dumps(old) + "\n"
    )
    (run_dir / "classify" / "contradictions.stub.json").write_text("{}")
    return run_dir, replay


def test_the_table_has_a_row_per_run_and_a_total_per_stage(tmp_path: Path) -> None:
    run_dir, replay = synthetic_run(tmp_path)

    rendered = table(run_costs(run_dir, replay, PRICES))

    rows = [line for line in rendered.splitlines() if line.startswith("| ") and "---" not in line]
    assert rows == [
        "| Stage | Run | Models | Requests | Input | Cache writes | Cache reads | Output | Cost "
        "| Prices of |",
        "| retrieve | last retrieval | PubMed E-utilities | 12 | 0 | 0 | 0 | 0 | $0.0000 | free |",
        "| extract | corpus-first2-small | synthetic-small | 2 | 500 | 0 | 0 | 0 | $1.2500 "
        "| 2026-01-01 |",
        "| extract | corpus-small | synthetic-small | 4 | 1,000,000 | 0 | 0 | 0 | $2.5000 "
        "| 2026-01-01 |",
        "| extract | dev-small | synthetic-small | 3 | 0 | 0 | 0 | 7 | $0.5000 | 2026-01-01 |",
        # Standard prices: 1M input at $1 and 100,000 output at $10 a million.
        "| normalize | small proposal abcdef01 | synthetic-small | 1 | 1,000,000 | 0 | 0 "
        "| 100,000 | $2.0000 | 2026-01-02 |",
        # Batch prices, half of 2 x 100,000 output at $10 a million.
        "| classify | dev-single-small.round1 | synthetic-small | 2 | 0 | 0 | 0 | 200,000 "
        "| $1.0000 | 2026-01-02 |",
        # Half of $1 input on one model, and $1 cache reads plus $2 output on the other.
        "| classify | eval-council | synthetic-peer, synthetic-small | 2 | 1,000,000 | 0 "
        "| 1,000,000 | 100,000 | $2.0000 | 2026-01-02 |",
        "| Stage | Runs | Cost |",
        "| retrieve | 1 | $0.0000 |",
        # The first stage's recordings are the full run's, so only corpus and dev count.
        "| extract | 3 | $3.0000 |",
        "| normalize | 1 | $2.0000 |",
        "| classify | 2 | $3.0000 |",
        "| All stages | 7 | $8.0000 |",
    ]
    assert rendered.endswith(
        "corpus-first2-small's recordings are corpus-small's, so the totals count them once.\n"
    )


def test_the_table_is_the_same_on_every_render(tmp_path: Path) -> None:
    run_dir, replay = synthetic_run(tmp_path)

    first = table(run_costs(run_dir, replay, PRICES))

    assert table(run_costs(run_dir, replay, PRICES)) == first


def test_extraction_keeps_the_cost_its_run_recorded(tmp_path: Path) -> None:
    # A later price file must not rewrite what was spent: extraction's manifest holds its cost.
    run_dir, replay = synthetic_run(tmp_path)
    doubled = PRICES.model_copy(update={"batch_discount": 1.0})

    extracted = [c for c in run_costs(run_dir, replay, doubled) if c.stage is Stage.EXTRACT]

    assert [c.cost_usd for c in extracted] == [1.25, 2.5, 0.5]


def test_a_first_stage_without_its_full_run_counts(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "synthetic"
    (run_dir / "extract").mkdir(parents=True)
    entry = extract_entry("synthetic-small", 2, 1.25)
    (run_dir / "extract" / "manifest.json").write_text(
        json.dumps({"runs": {"corpus-first2-small": entry}})
    )

    (only,) = run_costs(run_dir, tmp_path / "no-replay", PRICES)
    rendered = table([only])

    assert only.counted_in is None
    assert "| extract | 1 | $1.2500 |" in rendered
    assert "count them once" not in rendered


def test_a_question_with_no_runs_has_no_rows(tmp_path: Path) -> None:
    assert run_costs(tmp_path / "runs" / "none", tmp_path / "replay", PRICES) == []
    assert table([]).endswith("| All stages | 0 | $0.0000 |\n")


def recording(directory: Path, key: str, model: str, usage: dict[str, int]) -> None:
    response = {
        "id": f"msg_{key}",
        "model": model,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "{}"}],
        "usage": usage,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{key}.json").write_text(
        json.dumps({"key": key, "request": {}, "response": response})
    )


def test_the_corpus_row_counts_pairs_a_later_run_dropped(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "synthetic"
    (run_dir / "classify").mkdir(parents=True)
    kept = {"model": "synthetic-small", "usage": tokens(output=100_000)}
    (run_dir / "classify" / "corpus-council.jsonl").write_text(
        json.dumps({"pair_id": "kept", "calls": [kept]}) + "\n"
    )
    dev = {"model": "synthetic-small", "usage": tokens(input=1_000_000)}
    (run_dir / "classify" / "dev-council.jsonl").write_text(
        json.dumps({"pair_id": "dev", "calls": [dev]}) + "\n"
    )
    recordings = run_dir / "replay" / "classification" / "council"
    recording(recordings, "kept", "synthetic-small", tokens(output=100_000))
    # A pair the first run classified and a later exclusion dropped: paid for, not in results.
    recording(recordings, "dropped", "synthetic-small", tokens(output=300_000))
    recording(recordings, "dev", "synthetic-small", tokens(input=1_000_000))
    (recordings / "shared.json").write_text('{"system": "synthetic"}')

    costs = {c.run: c for c in run_costs(run_dir, tmp_path / "no-replay", PRICES)}

    # Half of 400,000 output at $10 a million; the dev pair stays in its own row.
    assert costs["corpus-council"].requests == 2
    assert costs["corpus-council"].cost_usd == 2.0
    assert costs["dev-council"].cost_usd == 0.5


def test_the_corpus_row_falls_back_to_its_results_without_recordings(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "synthetic"
    (run_dir / "classify").mkdir(parents=True)
    call = {"model": "synthetic-small", "usage": tokens(output=100_000)}
    (run_dir / "classify" / "corpus-council.jsonl").write_text(
        json.dumps({"pair_id": "kept", "calls": [call]}) + "\n"
    )

    (only,) = run_costs(run_dir, tmp_path / "no-replay", PRICES)

    assert only.cost_usd == 0.5
