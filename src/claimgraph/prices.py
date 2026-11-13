"""Prices from the dated prices.toml, and the dollars a request's token counts cost."""

import tomllib
from datetime import date
from pathlib import Path

from pydantic import NonNegativeFloat, PositiveFloat, PositiveInt

from claimgraph.llm import Usage
from claimgraph.models import Record

PRICES_FILE = Path("prices.toml")


class ModelPrices(Record):
    """US dollars per million tokens."""

    input: NonNegativeFloat
    output: NonNegativeFloat
    cache_write: NonNegativeFloat
    cache_read: NonNegativeFloat


class Prices(Record):
    checked: date
    batch_discount: PositiveFloat
    max_prompt_tokens: PositiveInt
    models: dict[str, ModelPrices]


def load_prices(path: Path = PRICES_FILE) -> Prices:
    with path.open("rb") as f:
        return Prices.model_validate(tomllib.load(f))


def model_prices(model: str, prices: Prices) -> ModelPrices:
    if model not in prices.models:
        raise ValueError(f"no price for {model} in the price file")
    return prices.models[model]


def cost(usage: Usage, model: str, prices: Prices, *, batch: bool) -> float:
    """What one request cost. The API counts cached tokens apart from input_tokens."""
    p = model_prices(model, prices)
    dollars = (
        usage.input_tokens * p.input
        + usage.cache_creation_input_tokens * p.cache_write
        + usage.cache_read_input_tokens * p.cache_read
        + usage.output_tokens * p.output
    ) / 1_000_000
    return dollars * (prices.batch_discount if batch else 1)
