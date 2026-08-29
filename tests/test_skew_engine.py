"""Unit tests for Skew analytical engine."""

from __future__ import annotations

from stock_monitor.skew_engine import build_verdict_sentence, process_skew_universe
from stock_monitor.skew_fetcher import RawChainData


def test_build_verdict_sentence() -> None:
    sentence = build_verdict_sentence(
        ticker="NVDA",
        sector="Technology",
        ret_1m=-0.045,
        rel_ret_spy=-0.065,
        normalized_skew=-0.18,
        sector_avg_norm_skew=0.10,
        sector_agreement=0.80,
        quadrant="Contrarian Bid",
        is_earnings_near=True,
        earnings_date="2025-05-22",
        sanity_passed=True,
        sanity_warning=None,
    )
    assert "NVDA is down 4.5% over 30d" in sentence
    assert "Options traders are paying 18.0% more for upside calls" in sentence
    assert "Technology shows 80% agreement" in sentence
    assert "[Contrarian Bid]" in sentence
    assert "[Warning: Event premium near earnings (2025-05-22)]" in sentence


def test_process_skew_universe_mock() -> None:
    strikes = [80.0, 90.0, 100.0, 110.0, 120.0]
    call_ivs = {s: 0.25 for s in strikes}
    put_ivs = {s: 0.25 for s in strikes}

    chain1 = RawChainData(
        ticker="AAPL",
        spot=100.0,
        ret_1m=-0.05,
        rvol=1.1,
        expiration="2025-05-16",
        dte_days=45,
        strikes=strikes,
        call_ivs=call_ivs,
        put_ivs=put_ivs,
    )
    chain2 = RawChainData(
        ticker="MSFT",
        spot=100.0,
        ret_1m=0.04,
        rvol=1.0,
        expiration="2025-05-16",
        dte_days=45,
        strikes=strikes,
        call_ivs=call_ivs,
        put_ivs=put_ivs,
    )

    records, sectors = process_skew_universe([chain1, chain2], spy_1m_ret=0.01)
    assert len(records) == 2
    tickers = {r.ticker for r in records}
    assert tickers == {"AAPL", "MSFT"}

    assert "Technology" in sectors
    tech = sectors["Technology"]
    assert tech.ticker_count == 2
