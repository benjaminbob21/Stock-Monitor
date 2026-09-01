"""Tests for buy-more (scale-in) lots: positions + basket legs.

Core guarantee of the feature: appending a lot re-averages entry_price as the
volume-weighted mean across buys, sums the quantity/shares, and every existing
P&L view (cost basis = qty × avg entry, basket pnl = value − budget) stays
correct with no view-code changes.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_monitor.baskets import BasketError, basket_view, buy_into_leg, create_basket
from stock_monitor.storage.db import Storage


class FakePrices:
    name = "fake"

    def __init__(self, quotes: dict[str, float]) -> None:
        self.quotes = quotes

    def get_quote(self, ticker: str) -> float | None:
        return self.quotes.get(ticker)

    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        return pd.DataFrame()


def _store() -> Storage:
    return Storage(":memory:")


# --------------------------------------------------------------------- storage


def test_add_position_seeds_first_lot() -> None:
    with _store() as store:
        store.add_position(
            "p1", "BLBD", dt.datetime(2026, 8, 1, 12), 23.0, 82, "buy", []
        )
        lots = store.list_lots("p1")
        assert len(lots) == 1
        assert lots[0]["price"] == pytest.approx(23.0)
        assert lots[0]["quantity"] == pytest.approx(1.0)
        assert store.get_position("p1")["lots"] == lots


def test_add_lot_vwap_reaverages() -> None:
    with _store() as store:
        store.add_position(
            "p1", "BLBD", dt.datetime(2026, 8, 1, 12), 23.0, 82, "buy", [], quantity=10.0
        )
        updated = store.add_lot(
            position_id="p1",
            bought_at=dt.datetime(2026, 8, 20, 12),
            price=20.0,
            quantity=10.0,
            note="the dip",
        )
        assert updated["quantity"] == pytest.approx(20.0)
        assert updated["entry_price"] == pytest.approx(21.50)  # VWAP of 10@23 + 10@20
        lots = store.list_lots("p1")
        assert [lot["quantity"] for lot in lots] == [10.0, 10.0]
        assert lots[1]["note"] == "the dip"


def test_delete_position_removes_lots() -> None:
    with _store() as store:
        store.add_position(
            "p1", "BLBD", dt.datetime(2026, 8, 1, 12), 23.0, 82, "buy", []
        )
        store.add_lot(
            position_id="p1", bought_at=dt.datetime.now(), price=20.0, quantity=5.0
        )
        assert store.delete_position("p1") is True
        assert store.list_lots("p1") == []


# ------------------------------------------------------------------- positions


def test_position_view_reports_multiple_lots() -> None:
    with _store() as store:
        store.add_position(
            "p1", "BLBD", dt.datetime(2026, 8, 1, 12), 23.0, 82, "buy", [], quantity=10.0
        )
        store.add_lot(
            position_id="p1", bought_at=dt.datetime.now(), price=20.0, quantity=10.0
        )
        view = store.get_position("p1")  # row with lots attached
        assert view["quantity"] == pytest.approx(20.0)
        assert view["entry_price"] == pytest.approx(21.50)
        assert len(view["lots"]) == 2
        # Combined cost basis math the UI will do: 20 sh × $21.50 = $430.
        assert view["quantity"] * view["entry_price"] == pytest.approx(430.0)


# -------------------------------------------------------------------- baskets


def test_buy_into_leg_dollars_grows_budget_and_vwaps() -> None:
    prices = FakePrices({"NVDA": 100.0, "MSFT": 200.0})
    with _store() as store:
        basket = create_basket(
            "Duo", 10_000.0, ["NVDA", "MSFT"], [40.0, 60.0], prices, store
        )
        nvda_leg = next(item for item in basket["items"] if item["ticker"] == "NVDA")

        # Price drops to 80; user buys $800 more → 10 shares at the new price.
        prices.quotes["NVDA"] = 80.0
        updated = buy_into_leg(
            nvda_leg["id"], prices, store, dollars=800.0, note="dip buy"
        )
        updated_leg = next(
            i for i in store.list_basket_items(updated["id"]) if i["ticker"] == "NVDA"
        )
        assert updated_leg["shares"] == pytest.approx(40.0 + 10.0)
        assert updated_leg["entry_price"] == pytest.approx(
            (40.0 * 100.0 + 10.0 * 80.0) / 50.0
        )  # 96.0
        assert updated_leg["budget"] == pytest.approx(4000.0 + 800.0)
        assert len(updated_leg["lots"]) == 2
        assert updated_leg["lots"][1]["note"] == "dip buy"

        # Basket pnl math: value 4000@100 still + 4000@80 vs budget 4800.
        view = basket_view(updated, prices)
        assert view["total_budget"] == pytest.approx(10_800.0)


def test_buy_into_leg_shares_rejects_double_spec() -> None:
    prices = FakePrices({"NVDA": 100.0})
    with _store() as store:
        basket = create_basket("Solo", 1_000.0, ["NVDA"], [100.0], prices, store)
        leg_id = basket["items"][0]["id"]
        with pytest.raises(BasketError):
            buy_into_leg(leg_id, prices, store)  # neither given
        with pytest.raises(BasketError):
            buy_into_leg(leg_id, prices, store, shares=1.0, dollars=1.0)
        with pytest.raises(BasketError):
            buy_into_leg(leg_id, prices, store, dollars=-5.0)
        # Neither-match id → None (not found)
        assert buy_into_leg("nope", prices, store, shares=1.0) is None


def test_buy_into_sold_leg_rejected() -> None:
    prices = FakePrices({"NVDA": 100.0})
    with _store() as store:
        basket = create_basket("Solo", 1_000.0, ["NVDA"], [100.0], prices, store)
        leg_id = basket["items"][0]["id"]
        store.sell_basket_item(leg_id, dt.datetime.now(), 110.0)
        with pytest.raises(BasketError, match="sold"):
            buy_into_leg(leg_id, prices, store, shares=1.0)


# ------------------------------------------------- explicit fill price / date


def test_buy_into_leg_explicit_price_and_date() -> None:
    """Logging an earlier trade: explicit price wins over the live quote."""
    prices = FakePrices({"NVDA": 100.0})  # live quote would be 100
    when = dt.datetime(2026, 8, 15, 9, 30)
    with _store() as store:
        basket = create_basket("Solo", 1_000.0, ["NVDA"], [100.0], prices, store)
        leg = basket["items"][0]
        # Leg seeded: 10 sh @ 100 (budget 1000). Top-up $500 @ explicit 125.
        buy_into_leg(
            leg["id"], prices, store, dollars=500.0, price=125.0, bought_at=when
        )
        updated = store.get_basket(basket["id"])
        updated["items"] = store.list_basket_items(basket["id"])
        leg2 = updated["items"][0]
        assert leg2["shares"] == pytest.approx(10.0 + 500.0 / 125.0)
        # VWAP of 10@100 + 4@125
        assert leg2["entry_price"] == pytest.approx((10 * 100 + 4 * 125) / 14.0)
        # Budget grows by actual cost regardless of price source.
        assert leg2["budget"] == pytest.approx(1_500.0)
        lots = leg2["lots"]
        # Lots sort oldest-first by bought_at: the backdated $500@125 buy
        # (Aug 15) sorts BEFORE the seeded lot (today).
        assert lots[0]["price"] == pytest.approx(125.0)
        assert lots[0]["bought_at"] == when.isoformat()
        assert lots[-1]["price"] == pytest.approx(100.0)


def test_buy_into_leg_rejects_bad_price() -> None:
    prices = FakePrices({"NVDA": 100.0})
    with _store() as store:
        basket = create_basket("Solo", 1_000.0, ["NVDA"], [100.0], prices, store)
        leg_id = basket["items"][0]["id"]
        with pytest.raises(BasketError, match="price"):
            buy_into_leg(leg_id, prices, store, dollars=100.0, price=0.0)
        with pytest.raises(BasketError, match="price"):
            buy_into_leg(leg_id, prices, store, dollars=100.0, price=-3.0)


def test_add_lot_backdated() -> None:
    """Storage honours arbitrary bought_at (UI date picker for late logging)."""
    with _store() as store:
        store.add_position(
            "p1", "BLBD", dt.datetime(2026, 8, 1, 12), 23.0, 82, "buy", [], quantity=10.0
        )
        store.add_lot(
            position_id="p1",
            bought_at=dt.datetime(2026, 7, 10, 15, 45),
            price=19.0,
            quantity=5.0,
        )
        position = store.get_position("p1")
        assert position["entry_price"] == pytest.approx((10 * 23 + 5 * 19) / 15.0)
        lots = position["lots"]
        # Oldest-first ordering: the backdated Jul-10 lot comes first.
        assert lots[0]["bought_at"] == "2026-07-10T15:45:00"
        assert lots[0]["price"] == pytest.approx(19.0)
