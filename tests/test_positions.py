"""Position tracking + exit-signal tests (network-free via fake providers)."""

from __future__ import annotations

from types import SimpleNamespace

from stock_monitor.positions import (
    exit_signal,
    list_position_views,
    open_position,
    sell_position,
)
from stock_monitor.storage import Storage


def test_exit_signal_bands_and_material_flags() -> None:
    assert exit_signal(70, []) == "hold"
    assert exit_signal(50, []) == "consider trimming / watch"
    assert exit_signal(30, []) == "consider selling"
    # A material negative flag forces a sell even at high conviction.
    assert exit_signal(90, ["negative_earnings"]) == "consider selling"


def test_open_track_and_sell_position(world: SimpleNamespace) -> None:
    with Storage(":memory:") as store:
        view = open_position(
            "AAA",
            model=world.model,
            model_version=world.version,
            price_provider=world.price_provider,
            fundamental_provider=world.fundamental_provider,
            storage=store,
        )
        assert view["ticker"] == "AAA"
        assert view["status"] == "open"
        assert view["entry_price"] > 0
        assert "signal" in view and view["expert_view"]
        assert view["current_price"] > 0
        assert "conviction_change" in view

        position_id = view["id"]
        views = list_position_views(
            world.model,
            world.version,
            world.price_provider,
            world.fundamental_provider,
            store,
        )
        assert len(views) == 1 and views[0]["ticker"] == "AAA"

        sold = sell_position(
            position_id,
            model=world.model,
            model_version=world.version,
            price_provider=world.price_provider,
            fundamental_provider=world.fundamental_provider,
            storage=store,
        )
        assert sold is not None
        assert sold["status"] == "sold"
        assert sold["sold_price"] is not None
        assert sold["signal"] == "sold"


def test_sell_unknown_position_returns_none(world: SimpleNamespace) -> None:
    with Storage(":memory:") as store:
        result = sell_position(
            "does-not-exist",
            model=world.model,
            model_version=world.version,
            price_provider=world.price_provider,
            fundamental_provider=world.fundamental_provider,
            storage=store,
        )
        assert result is None
