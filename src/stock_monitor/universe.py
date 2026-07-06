"""Investable universe for the daily scan.

Phase 3 starts with a curated, sector-diversified set of liquid large-caps so the
scan is fast and reliable on free data. This is deliberately *not* survivorship-free
(it's today's names) — fine for "what looks good to buy now", but not for honest
backtests (see build-plan §8). Widening toward the full S&P 500 → Russell 3000 and a
historical-membership universe is the Phase 3/5 expansion path.
"""

from __future__ import annotations

# ~48 liquid names across sectors (a starter universe, easily expanded). Kept under
# Tiingo's free 50-requests/hour cap so a full daily scan stays reliable and $0. The
# scan skips any ticker that fails, so this can grow, but scaling to hundreds wants a
# no-cap provider (yfinance) or a paid tier — see build-plan §3/§5 expansion path.
DEFAULT_UNIVERSE: tuple[str, ...] = (
    # Broad-market ETFs (the S&P/Nasdaq baselines the model measures everything against).
    "SPY", "QQQ",
    # Tech / comms
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "ADBE", "CRM", "ORCL", "CSCO",
    # Semiconductors (beyond the mega-caps)
    "AMD", "MU", "QCOM", "TXN",
    # Cybersecurity / software
    "PANW",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "KO", "PEP", "COST", "WMT",
    # Consumer-internet / mobility
    "UBER", "ABNB",
    # Financials
    "JPM", "BAC", "V", "MA", "GS",
    # Health / biotech / medtech
    "UNH", "JNJ", "LLY", "PFE", "ISRG", "VRTX", "REGN",
    # Energy / industrial / energy-transition
    "XOM", "CVX", "CAT", "DE", "GE", "ENPH",
    # Income / REIT / utility
    "O", "NEE",
)


def get_universe() -> list[str]:
    """Return the default scan universe."""
    return list(DEFAULT_UNIVERSE)
