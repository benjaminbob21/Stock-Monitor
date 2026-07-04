"""Earnings cap + alert-debounce tests (network-free)."""

from __future__ import annotations

import datetime as dt

from stock_monitor.earnings import (
    EarningsProvider,
    NullEarningsProvider,
    days_until_earnings,
)
from stock_monitor.service import apply_risk_caps
from stock_monitor.storage import Storage


class FakeEarningsProvider(EarningsProvider):
    name = "fake-earnings"

    def __init__(self, date: dt.date | None) -> None:
        self._date = date

    def next_earnings_date(self, ticker: str) -> dt.date | None:
        return self._date


def test_earnings_soon_caps_conviction() -> None:
    row = {"vol_3m": 0.2, "fundamentals_known_on": "2025-01-01"}
    capped, caps = apply_risk_caps(85, row, 100.0, days_to_earnings=3)
    assert capped <= 55
    assert "earnings_soon_cap" in caps


def test_earnings_far_out_no_cap() -> None:
    row = {"vol_3m": 0.2, "fundamentals_known_on": "2025-01-01"}
    capped, caps = apply_risk_caps(85, row, 100.0, days_to_earnings=30)
    assert capped == 85
    assert "earnings_soon_cap" not in caps


def test_days_until_earnings() -> None:
    today = dt.date(2025, 1, 1)
    provider = FakeEarningsProvider(dt.date(2025, 1, 4))
    assert days_until_earnings(provider, "X", today) == 3
    assert days_until_earnings(NullEarningsProvider(), "X", today) is None


def test_alert_debounce() -> None:
    with Storage(":memory:") as store:
        assert store.recent_alert("AAA", "negative_news", 24) is False
        store.record_alert("AAA", "negative_news", "-0.5")
        assert store.recent_alert("AAA", "negative_news", 24) is True
        assert store.recent_alert("AAA", "earnings_soon", 24) is False
        assert store.count("alerts") == 1
