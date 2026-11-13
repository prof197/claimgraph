"""Prices: the dated price file, and dollars from token counts."""

from datetime import date
from pathlib import Path

import pytest

from claimgraph.llm import Usage
from claimgraph.prices import Prices, cost, load_prices

ROOT = Path(__file__).parents[1]
PRICES = load_prices(ROOT / "prices.toml")


def usage(input: int = 0, output: int = 0, write: int = 0, read: int = 0) -> Usage:
    return Usage(
        input_tokens=input,
        output_tokens=output,
        cache_creation_input_tokens=write,
        cache_read_input_tokens=read,
    )


def test_the_price_file_is_dated() -> None:
    assert isinstance(PRICES.checked, date)


def test_batch_prices_match_the_pricing_page() -> None:
    # Per million tokens through the batch API, as the pricing page listed them when checked.
    haiku = usage(input=1_000_000, output=1_000_000)
    assert cost(haiku, "claude-haiku-5-5", PRICES, batch=True) == pytest.approx(0.05 + 0.25)
    assert cost(haiku, "claude-sonnet-5-5", PRICES, batch=True) == pytest.approx(1 + 5)


def test_cache_reads_cost_less_than_input() -> None:
    # 0.1x on Haiku 5.5 and 0.05x on Sonnet 5.5; writes at 1.25x for the five-minute cache.
    for model, read_share in (("claude-haiku-5-5", 0.1), ("claude-sonnet-5-5", 0.05)):
        input_price = cost(usage(input=1_000_000), model, PRICES, batch=False)
        read = cost(usage(read=1_000_000), model, PRICES, batch=False)
        write = cost(usage(write=1_000_000), model, PRICES, batch=False)
        assert read == pytest.approx(input_price * read_share)
        assert write == pytest.approx(input_price * 1.25)


def test_the_batch_discount_halves_every_token() -> None:
    mixed = usage(input=1000, output=500, write=3000, read=9000)

    assert cost(mixed, "claude-haiku-5-5", PRICES, batch=True) == pytest.approx(
        cost(mixed, "claude-haiku-5-5", PRICES, batch=False) / 2
    )


def test_a_model_without_a_price_is_an_error() -> None:
    with pytest.raises(ValueError, match="no price for synthetic-model"):
        cost(usage(input=1), "synthetic-model", PRICES, batch=True)


def test_prices_load_from_any_file(tmp_path: Path) -> None:
    path = tmp_path / "prices.toml"
    path.write_text(
        "checked = 2026-01-01\nbatch_discount = 0.5\nmax_prompt_tokens = 100000\n\n"
        "[models.synthetic-model]\ninput = 1.0\noutput = 2.0\ncache_write = 1.25\n"
        "cache_read = 0.1\n"
    )

    prices = load_prices(path)

    assert isinstance(prices, Prices)
    assert cost(usage(output=1_000_000), "synthetic-model", prices, batch=False) == 2.0
