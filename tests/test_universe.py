"""Universe selection tests (network-free)."""

from __future__ import annotations

from types import SimpleNamespace

from stock_monitor import universe
from stock_monitor.universe import DEFAULT_UNIVERSE, get_scan_universe, get_universe


def test_default_universe_includes_baseline_etfs() -> None:
    names = set(get_universe())
    assert {"SPY", "QQQ"} <= names


def test_scan_universe_default_mode_uses_curated_list() -> None:
    settings = SimpleNamespace(scan_universe="default")
    assert get_scan_universe(settings) == list(DEFAULT_UNIVERSE)


def test_scan_universe_missing_setting_falls_back_to_default() -> None:
    assert get_scan_universe(None) == list(DEFAULT_UNIVERSE)


def test_scan_universe_sp500_uses_fetch(monkeypatch) -> None:
    fake = ["AAA", "BBB", "SPY"]
    monkeypatch.setattr(universe, "fetch_sp500_symbols", lambda: fake)
    settings = SimpleNamespace(scan_universe="sp500")
    assert get_scan_universe(settings) == fake


def test_fetch_sp500_falls_back_when_offline(monkeypatch, tmp_path) -> None:
    # No cache + a failing fetch must degrade to DEFAULT_UNIVERSE, never raise.
    monkeypatch.setenv("STOCK_MONITOR_DATA_DIR", str(tmp_path))

    def _boom(*_args, **_kwargs):
        raise RuntimeError("offline")

    import pandas as pd

    monkeypatch.setattr(pd, "read_html", _boom)
    assert universe.fetch_sp500_symbols() == list(DEFAULT_UNIVERSE)
