"""SymbolDirectory: name lookup + name/ticker search (network mocked)."""

from __future__ import annotations

from typing import Any

import pytest

from stock_monitor.symbols import SymbolDirectory

_SAMPLE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    "3": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA Corporation"},
    "4": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
}


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture
def directory(monkeypatch: pytest.MonkeyPatch) -> SymbolDirectory:
    d = SymbolDirectory()
    monkeypatch.setattr(d._session, "get", lambda *a, **k: _FakeResponse(_SAMPLE))
    return d


def test_name_for_known_and_unknown(directory: SymbolDirectory) -> None:
    assert directory.name_for("aapl") == "Apple Inc."
    assert directory.name_for("MSFT") == "Microsoft Corporation"
    assert directory.name_for("ZZZZ") is None


def test_search_by_company_name(directory: SymbolDirectory) -> None:
    results = directory.search("tesla")
    assert results[0].ticker == "TSLA"
    assert results[0].name == "Tesla, Inc."


def test_search_exact_ticker_ranks_first(directory: SymbolDirectory) -> None:
    results = directory.search("AAPL")
    assert results[0].ticker == "AAPL"


def test_search_ticker_prefix(directory: SymbolDirectory) -> None:
    tickers = [m.ticker for m in directory.search("NV")]
    assert "NVDA" in tickers


def test_search_empty_query_returns_nothing(directory: SymbolDirectory) -> None:
    assert directory.search("") == []
    assert directory.search("   ") == []


def test_search_respects_limit(directory: SymbolDirectory) -> None:
    # A substring shared by several names, capped to 1 result.
    assert len(directory.search("inc", limit=1)) == 1
