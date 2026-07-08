"""Investable universe for the daily scan.

Phase 3 starts with a curated, sector-diversified set of liquid large-caps so the
scan is fast and reliable on free data. This is deliberately *not* survivorship-free
(it's today's names) — fine for "what looks good to buy now", but not for honest
backtests (see build-plan §8). Widening toward the full S&P 500 → Russell 3000 and a
historical-membership universe is the Phase 3/5 expansion path.
"""

from __future__ import annotations

import os

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


# S&P 500 constituents change slowly; we cache the fetched list so a transient network
# blip (or Wikipedia layout change) never breaks the nightly scan.
_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _cache_path() -> os.PathLike[str] | str:
    root = os.environ.get("STOCK_MONITOR_DATA_DIR", "data")
    return os.path.join(root, "sp500_symbols.json")


def _normalize(symbol: str) -> str:
    """Map index tickers to yfinance form (e.g. ``BRK.B`` -> ``BRK-B``)."""
    return symbol.strip().upper().replace(".", "-")


def fetch_sp500_symbols(*, use_cache: bool = True) -> list[str]:
    """Return current S&P 500 tickers, cached to disk with a graceful fallback.

    Tries Wikipedia (no key, free); on any failure falls back to the on-disk cache,
    then to :data:`DEFAULT_UNIVERSE`. Never raises — the nightly scan must not break
    just because a fetch failed. The broad-market ETFs (SPY/QQQ) are always included
    so the model keeps its baselines.
    """
    import json
    import os

    cache = _cache_path()

    if use_cache and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, list) and cached:
                return cached
        except Exception:  # noqa: BLE001 — a bad cache must not break the scan
            pass

    try:
        from io import StringIO

        import pandas as pd
        import requests

        # Wikipedia blocks the default urllib user-agent (HTTP 403), so fetch the page
        # ourselves with a browser-like UA and hand the HTML to pandas.
        resp = requests.get(
            _SP500_WIKI_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; stock-monitor/1.0)"},
            timeout=20,
        )
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        symbols = [_normalize(s) for s in tables[0]["Symbol"].astype(str).tolist()]
        merged = sorted({*symbols, *(_normalize(s) for s in DEFAULT_UNIVERSE)})
        if merged:
            try:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                with open(cache, "w", encoding="utf-8") as fh:
                    json.dump(merged, fh)
            except Exception:  # noqa: BLE001 — caching is best-effort
                pass
            return merged
    except Exception:  # noqa: BLE001 — fall through to the static fallback
        pass

    return list(DEFAULT_UNIVERSE)


def get_scan_universe(settings: object | None = None) -> list[str]:
    """Return the universe for the nightly scan based on ``settings.scan_universe``.

    ``"sp500"`` fetches the full index (discovery of names you don't already track);
    anything else uses the curated :data:`DEFAULT_UNIVERSE`. The scan skips any ticker
    that fails, so breadth is safe.
    """
    mode = str(getattr(settings, "scan_universe", "default")).lower()
    if mode == "sp500":
        return fetch_sp500_symbols()
    return list(DEFAULT_UNIVERSE)

