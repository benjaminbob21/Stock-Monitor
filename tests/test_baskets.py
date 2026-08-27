"""Tests for joint portfolios (baskets): budget split, valuation, contributions."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_monitor.baskets import (
    BasketError,
    basket_view,
    create_basket,
    validate_weights,
)
from stock_monitor.storage.db import Storage


class FakePrices:
    """Deterministic price provider: fixed quotes + SPY daily bar series."""

    name = "fake"

    def __init__(self, quotes: dict[str, float], spy: list[float] | None = None) -> None:
        self.quotes = quotes
        self.spy = spy or [100.0]

    def get_quote(self, ticker: str) -> float | None:
        return self.quotes.get(ticker)

    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if ticker != "SPY" or len(self.spy) < 2:
            return pd.DataFrame()
        return pd.DataFrame({"close": self.spy})


def _store() -> Storage:
    return Storage(":memory:")


def test_validate_weights_rejects_bad_splits() -> None:
    with pytest.raises(BasketError):
        validate_weights([])
    with pytest.raises(BasketError):
        validate_weights([{"pct": 60}, {"pct": 30}])  # sums to 90
    with pytest.raises(BasketError):
        validate_weights([{"pct": -10}, {"pct": 110}])
    validate_weights([{"pct": 40}, {"pct": 60}])  # OK


def test_create_basket_snapshots_fractional_shares() -> None:
    prices = FakePrices({"NVDA": 100.0, "MSFT": 200.0})
    with _store() as store:
        basket = create_basket(
            "Growth duo",
            total_budget=10_000.0,
            tickers=["NVDA", "MSFT"],
            pcts=[40.0, 60.0],
            price_provider=prices,
            storage=store,
        )
        assert basket["total_budget"] == 10_000.0
        legs = {leg["ticker"]: leg for leg in basket["items"]}
        assert legs["NVDA"]["budget"] == pytest.approx(4000.0)
        assert legs["NVDA"]["shares"] == pytest.approx(40.0)  # fractional allowed
        assert legs["MSFT"]["shares"] == pytest.approx(30.0)


def test_create_basket_rejects_unpriceable_and_bad_budget() -> None:
    prices = FakePrices({"NVDA": 100.0})  # MSFT missing
    with _store() as store:
        with pytest.raises(BasketError):
            create_basket("x", 1_000.0, ["NVDA", "MSFT"], [50, 50], prices, store)
        with pytest.raises(BasketError):
            create_basket("x", 0.0, ["NVDA"], [100], prices, store)


def test_basket_view_headline_and_contributions() -> None:
    prices = FakePrices(
        {"NVDA": 100.0, "MSFT": 200.0},
        spy=[100.0, 110.0],
    )
    with _store() as store:
        basket = create_basket(
            "duo", 10_000.0, ["NVDA", "MSFT"], [40, 60], prices, store
        )
        # Market moves after entry.
        prices.quotes.update({"NVDA": 125.0, "MSFT": 180.0})
        view = basket_view(basket, prices)

        # Whole-capital headline.
        assert view["current_value"] == pytest.approx(4000 * 1.25 + 6000 * 0.9)
        assert view["return_pct"] == pytest.approx(4.0, abs=0.01)
        assert view["pnl"] == pytest.approx(view["current_value"] - 10_000.0)

        legs = {leg["ticker"]: leg for leg in view["legs"]}
        nvda, msft = legs["NVDA"], legs["MSFT"]
        assert nvda["leg_return_pct"] == pytest.approx(25.0)
        assert msft["leg_return_pct"] == pytest.approx(-10.0)
        # Contribution to the whole = weight × leg return (in percentage points).
        assert nvda["contribution_points"] == pytest.approx(0.4 * 25.0)
        assert msft["contribution_points"] == pytest.approx(0.6 * -10.0)
        # Contributions must sum to the headline move.
        total_points = sum(
            leg["contribution_points"] for leg in view["legs"]
        )
        assert total_points == pytest.approx(view["return_pct"], abs=0.05)

        # Same-budget SPY read exists and excess is its difference.
        assert view["benchmark_return_pct"] == pytest.approx(10.0)
        assert view["excess_vs_spy_pct"] == pytest.approx(4.0 - 10.0, abs=0.02)


def test_sold_leg_parks_value_at_exit_price() -> None:
    prices = FakePrices({"NVDA": 120.0})
    with _store() as store:
        basket = create_basket("solo", 1_000.0, ["NVDA"], [100], prices, store)
        leg = basket["items"][0]
        store.sell_basket_item(leg["id"], dt.datetime.now(), 150.0)
        items = store.list_basket_items(basket["id"])
        assert items[0]["status"] == "sold"
        view = basket_view({**basket, "items": items}, prices)
        assert view["current_value"] == pytest.approx(1_250.0)


def test_close_basket_closes_all_legs() -> None:
    prices = FakePrices({"NVDA": 120.0, "MSFT": 200.0})
    with _store() as store:
        basket = create_basket(
            "duo", 1_000.0, ["NVDA", "MSFT"], [50, 50], prices, store
        )
        store.close_basket(basket["id"], dt.datetime.now())
        stored = store.get_basket(basket["id"])
        assert stored is not None and stored["status"] == "closed"
        assert all(
            i["status"] == "sold" for i in store.list_basket_items(basket["id"])
        )
