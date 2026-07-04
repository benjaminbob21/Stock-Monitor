"""Shared test fixtures: synthetic data, fake providers, and a trained test world.

Everything here is network-free and deterministic (seeded), so the whole suite runs
offline and reproducibly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stock_monitor.features.builder import build_training_frame
from stock_monitor.models.registry import compute_model_version
from stock_monitor.models.scorer import train_model
from stock_monitor.providers.base import (
    PRICE_COLUMNS,
    FundamentalFact,
    FundamentalProvider,
    PriceProvider,
)


def random_walk(
    seed: int, days: int = 2200, drift: float = 0.0004, vol: float = 0.02
) -> pd.DataFrame:
    """Deterministic random-walk OHLCV frame (close-only, adjusted-style)."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, vol, days)
    close = 100.0 * np.exp(np.cumsum(returns))
    idx = pd.bdate_range("2016-01-04", periods=days)
    frame = pd.DataFrame(
        {c: close if c != "volume" else np.full(days, 1_000_000) for c in PRICE_COLUMNS},
        index=idx,
    )
    frame.index.name = "date"
    return frame


def make_facts(ticker: str = "AAA") -> list[FundamentalFact]:
    end, filed = dt.date(2016, 12, 31), dt.date(2017, 2, 1)

    def fact(concept: str, value: float) -> FundamentalFact:
        return FundamentalFact(ticker, concept, value, "USD", end, filed, "10-K")

    return [
        fact("NetIncomeLoss", 60.0),
        fact("StockholdersEquity", 500.0),
        fact("Assets", 1000.0),
        fact("Liabilities", 400.0),
        fact("Revenues", 250.0),
        fact("NetCashProvidedByUsedInOperatingActivities", 90.0),
        fact("PaymentsToAcquirePropertyPlantAndEquipment", 20.0),
        fact("CommonStockSharesOutstanding", 1000.0),
    ]


class FakePriceProvider(PriceProvider):
    name = "fake-price"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = frames

    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        return self._frames.get(ticker, pd.DataFrame())


class FakeFundamentalProvider(FundamentalProvider):
    name = "fake-fundamentals"

    def __init__(self, facts: dict[str, list[FundamentalFact]]) -> None:
        self._facts = facts

    def get_fundamentals(
        self, ticker: str, concepts: Sequence[str] | None = None
    ) -> list[FundamentalFact]:
        return self._facts.get(ticker, [])


@pytest.fixture
def world() -> SimpleNamespace:
    """A ready-to-use trained model + fake providers over synthetic data."""
    ticker = "AAA"
    prices = random_walk(seed=1)
    benchmark = random_walk(seed=2, drift=0.0003)
    facts = make_facts(ticker)

    frame = build_training_frame(ticker, prices, facts, benchmark, label_window_months=12)
    assert frame["label"].nunique() == 2, "test data must contain both classes"

    model = train_model(frame)
    version = compute_model_version(model)

    return SimpleNamespace(
        ticker=ticker,
        prices=prices,
        benchmark=benchmark,
        facts=facts,
        frame=frame,
        model=model,
        version=version,
        price_provider=FakePriceProvider({ticker: prices, "SPY": benchmark}),
        fundamental_provider=FakeFundamentalProvider({ticker: facts}),
    )
