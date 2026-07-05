"""Tiingo / EODHD provider parsing + factory tests (HTTP monkeypatched, no network)."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.config import Settings
from stock_monitor.providers import get_price_provider
from stock_monitor.providers.base import PRICE_COLUMNS, PriceProvider
from stock_monitor.providers.eodhd_provider import (
    EODHDNewsProvider,
    EODHDProvider,
    _eodhd_symbol,
)
from stock_monitor.providers.tiingo_provider import TiingoProvider
from stock_monitor.providers.yfinance_provider import YFinanceProvider


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None

    def json(self) -> object:
        return self._payload


def test_tiingo_parses_adjusted_ohlcv(monkeypatch) -> None:
    # Real Tiingo payloads include BOTH raw (open/high/...) AND adjusted (adjOpen/...)
    # columns; the provider must select the adjusted ones without duplicating columns.
    payload = [
        {
            "date": "2024-01-02T00:00:00.000Z",
            "open": 9.9, "high": 10.9, "low": 9.4, "close": 10.4, "volume": 900,
            "adjOpen": 10.0, "adjHigh": 11.0, "adjLow": 9.5,
            "adjClose": 10.5, "adjVolume": 1000,
        },
        {
            "date": "2024-01-03T00:00:00.000Z",
            "open": 10.4, "high": 11.9, "low": 9.9, "close": 11.4, "volume": 1900,
            "adjOpen": 10.5, "adjHigh": 12.0, "adjLow": 10.0,
            "adjClose": 11.5, "adjVolume": 2000,
        },
    ]
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: _FakeResp(payload)
    )
    df = TiingoProvider("key").get_prices("AAPL", dt.date(2024, 1, 1), dt.date(2024, 1, 4))

    assert list(df.columns) == list(PRICE_COLUMNS)  # exactly 5, no duplicates
    assert df.shape == (2, 5)
    assert df.index.name == "date"
    assert df.iloc[0]["close"] == 10.5  # adjusted, not the raw 10.4
    assert len(df) == 2


def test_eodhd_scales_ohlc_by_adjustment_factor(monkeypatch) -> None:
    # close 20 vs adjusted_close 10 -> factor 0.5 applied to open/high/low.
    payload = [
        {
            "date": "2024-01-02", "open": 20.0, "high": 22.0, "low": 18.0,
            "close": 20.0, "adjusted_close": 10.0, "volume": 1000,
        }
    ]
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(payload))
    df = EODHDProvider("key").get_prices("AAPL", dt.date(2024, 1, 1), dt.date(2024, 1, 3))

    assert df.iloc[0]["close"] == 10.0
    assert df.iloc[0]["open"] == 10.0  # 20 * 0.5
    assert df.iloc[0]["volume"] == 2000  # 1000 / 0.5


def test_eodhd_news_range_parses_items(monkeypatch) -> None:
    payload = [
        {"title": "Acme beats", "link": "http://x", "date": "2024-03-01T10:00:00+00:00"},
        {"title": "", "link": "http://y", "date": "2024-03-02T10:00:00+00:00"},  # dropped
    ]
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp(payload))
    items = EODHDNewsProvider("key").get_news_range(
        "AAPL", dt.date(2024, 3, 1), dt.date(2024, 3, 3)
    )
    assert len(items) == 1
    assert items[0].headline == "Acme beats"
    assert items[0].published == dt.datetime(2024, 3, 1, 10, 0)


def test_eodhd_symbol_normalisation() -> None:
    assert _eodhd_symbol("aapl") == "AAPL.US"
    assert _eodhd_symbol("BMW.XETRA") == "BMW.XETRA"


def test_factory_defaults_to_yfinance() -> None:
    assert isinstance(get_price_provider(None), YFinanceProvider)
    assert isinstance(get_price_provider(Settings(price_provider="yfinance")), YFinanceProvider)


def test_factory_selects_keyed_providers() -> None:
    s = Settings(price_provider="tiingo", tiingo_api_key="k")
    assert isinstance(get_price_provider(s), TiingoProvider)

    s = Settings(price_provider="eodhd", eodhd_api_key="k")
    assert isinstance(get_price_provider(s), EODHDProvider)


def test_factory_falls_back_when_key_missing() -> None:
    # Asked for tiingo but no key set -> must not break, falls back to yfinance.
    s = Settings(price_provider="tiingo", tiingo_api_key="")
    assert isinstance(get_price_provider(s), YFinanceProvider)
    assert isinstance(get_price_provider(s), PriceProvider)
