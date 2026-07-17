"""Macro (FRED) PIT lookup + backfill orchestrator + FRED provider parsing.

Network-free: the FRED HTTP call is monkeypatched; the lookup and backfill run against
in-memory structures / DuckDB.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from stock_monitor.features.builder import FEATURE_COLUMNS
from stock_monitor.macro import (
    MACRO_FEATURE_COLUMNS,
    _to_yoy,
    backfill_macro,
    make_macro_lookup,
)
from stock_monitor.providers.fred_provider import FredError, FredProvider, MacroObs
from stock_monitor.storage.db import Storage


def test_macro_columns_are_in_feature_columns() -> None:
    assert set(MACRO_FEATURE_COLUMNS).issubset(FEATURE_COLUMNS)
    assert "macro_yield_curve" in MACRO_FEATURE_COLUMNS


def _macro_df(rows: list[tuple[str, dt.date, dt.date, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["series_id", "obs_date", "realtime_start", "value"]
    )


def test_lookup_is_point_in_time() -> None:
    df = _macro_df(
        [
            ("T10Y2Y", dt.date(2024, 1, 1), dt.date(2024, 1, 2), 0.5),
            ("T10Y2Y", dt.date(2024, 2, 1), dt.date(2024, 2, 2), 0.3),
        ]
    )
    lookup = make_macro_lookup(df)

    # Before anything was released -> series absent (stays NaN downstream).
    assert lookup(dt.date(2023, 12, 31)) == {}
    # Only the Jan value is knowable mid-January.
    assert lookup(dt.date(2024, 1, 15)) == {"macro_yield_curve": 0.5}
    # By mid-February the fresher reading is available.
    assert lookup(dt.date(2024, 2, 15)) == {"macro_yield_curve": 0.3}


def test_lookup_uses_first_release_not_later_revision() -> None:
    df = _macro_df(
        [
            # Same observation date, revised later — the revision must not leak back.
            ("CPIAUCSL", dt.date(2024, 1, 1), dt.date(2024, 1, 15), 3.0),
            ("CPIAUCSL", dt.date(2024, 1, 1), dt.date(2024, 3, 1), 3.4),
        ]
    )
    lookup = make_macro_lookup(df)
    # Even well after the revision, we use the value first published for that period.
    assert lookup(dt.date(2024, 6, 1)) == {"macro_cpi_yoy": 3.0}


def test_lookup_handles_multiple_series() -> None:
    df = _macro_df(
        [
            ("T10Y2Y", dt.date(2024, 1, 1), dt.date(2024, 1, 2), 0.5),
            ("DFF", dt.date(2024, 1, 1), dt.date(2024, 1, 2), 5.25),
            ("UNRATE", dt.date(2023, 12, 1), dt.date(2024, 1, 5), 3.7),
        ]
    )
    result = make_macro_lookup(df)(dt.date(2024, 1, 20))
    assert result == {
        "macro_yield_curve": 0.5,
        "macro_fed_funds": 5.25,
        "macro_unemployment": 3.7,
    }


def test_empty_lookup_returns_empty_dict() -> None:
    empty = pd.DataFrame(columns=["series_id", "obs_date", "realtime_start", "value"])
    lookup = make_macro_lookup(empty)
    assert lookup(dt.date(2024, 1, 1)) == {}


def test_to_yoy_is_point_in_time() -> None:
    obs = [
        MacroObs(dt.date(2023, 1, 1), dt.date(2023, 2, 15), 100.0),
        MacroObs(dt.date(2024, 1, 1), dt.date(2024, 2, 15), 103.0),
    ]
    out = _to_yoy(obs)
    assert len(out) == 1
    assert out[0].obs_date == dt.date(2024, 1, 1)
    # YoY inherits the current month's release date (when it became knowable).
    assert out[0].realtime_start == dt.date(2024, 2, 15)
    assert round(out[0].value, 6) == 3.0


# --- backfill orchestrator ---------------------------------------------------


class _FakeFred:
    name = "fred"

    def __init__(self, by_series: dict[str, list[MacroObs]]) -> None:
        self._by_series = by_series
        self.calls: list[str] = []

    def get_series_vintages(
        self, series_id: str, *, observation_start: object = None
    ) -> list[MacroObs]:
        self.calls.append(series_id)
        return self._by_series.get(series_id, [])


def test_backfill_macro_stores_and_reads_back() -> None:
    provider = _FakeFred(
        {
            "T10Y2Y": [MacroObs(dt.date(2024, 1, 1), dt.date(2024, 1, 2), 0.5)],
            "DFF": [MacroObs(dt.date(2024, 1, 1), dt.date(2024, 1, 2), 5.25)],
        }
    )
    with Storage(":memory:") as store:
        stored = backfill_macro(provider, store)
        assert stored == 2
        # All five series were requested.
        assert len(provider.calls) == 5
        back = store.read_macro_series("T10Y2Y")
        assert len(back) == 1
        lookup = make_macro_lookup(store.read_macro_series())
        assert lookup(dt.date(2024, 1, 10)) == {
            "macro_yield_curve": 0.5,
            "macro_fed_funds": 5.25,
        }

        # Re-running is idempotent (upsert on the vintage key).
        backfill_macro(provider, store)
        assert store.count("macro_series") == 2


# --- FRED provider parsing ---------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_provider_parses_and_skips_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "observations": [
            {"date": "2024-01-01", "realtime_start": "2024-01-02", "value": "0.5"},
            {"date": "2024-01-02", "realtime_start": "2024-01-03", "value": "."},  # NA
            {"date": "2024-01-03", "realtime_start": "2024-01-04", "value": "0.7"},
        ]
    }
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(payload))
    obs = FredProvider("KEY").get_series_vintages("T10Y2Y")
    assert [o.value for o in obs] == [0.5, 0.7]
    assert obs[0].obs_date == dt.date(2024, 1, 1)
    assert obs[0].realtime_start == dt.date(2024, 1, 2)


def test_provider_raises_on_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: _FakeResponse({"error_message": "Bad API key"}),
    )
    with pytest.raises(FredError):
        FredProvider("KEY").get_series_vintages("T10Y2Y")


def test_provider_requires_key() -> None:
    with pytest.raises(ValueError):
        FredProvider("")
