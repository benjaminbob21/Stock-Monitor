"""Investable universe for the daily scan.

Phase 3 starts with a curated, sector-diversified set of liquid large-caps so the
scan is fast and reliable on free data. This is deliberately *not* survivorship-free
(it's today's names) — fine for "what looks good to buy now", but not for honest
backtests (see build-plan §8). Widening toward the full S&P 500 → Russell 3000 and a
historical-membership universe is the Phase 3/5 expansion path.
"""

from __future__ import annotations

# ~30 liquid large-caps across sectors (a starter universe, easily expanded).
DEFAULT_UNIVERSE: tuple[str, ...] = (
    # Broad-market ETFs (the S&P/Nasdaq baselines the model measures everything against).
    "SPY", "QQQ",
    # Tech / comms
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "ADBE", "CRM", "ORCL", "CSCO",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "KO", "PEP", "COST", "WMT",
    # Financials
    "JPM", "BAC", "V", "MA", "GS",
    # Health
    "UNH", "JNJ", "LLY", "PFE",
    # Energy / industrial
    "XOM", "CVX", "CAT",
)


def get_universe() -> list[str]:
    """Return the default scan universe."""
    return list(DEFAULT_UNIVERSE)
