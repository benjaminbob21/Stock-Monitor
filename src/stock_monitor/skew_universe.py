"""Skew Map Universe & Sector definitions.

Per the 'Build Your Own Skew Map' methodology:
- Core universe covers liquid, diversified names across sectors.
- Sector classification is required for Trap #2 (raw vol comparison across sectors)
  and Trap #3 (measuring sector agreement: what % of names in a sector lean the same way).
- Sector benchmark ETFs (XLK, XLE, XLF, etc.) and Market baselines (SPY, QQQ, IWM)
  are tracked alongside single names.
"""

from __future__ import annotations

import logging

from stock_monitor.universe import DEFAULT_UNIVERSE, fetch_sp500_symbols

logger = logging.getLogger(__name__)

# Sector ETFs
SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}

# Market benchmark ETFs
MARKET_ETFS: tuple[str, ...] = ("SPY", "QQQ", "IWM")

# Curated sector mappings for common large-cap tickers
SECTOR_MAP: dict[str, str] = {
    # Broad / ETFs
    "SPY": "Broad Market",
    "QQQ": "Broad Market",
    "IWM": "Broad Market",
    "XLK": "Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    # Technology
    "AAPL": "Technology",
    "MSFT": "Technology",
    "NVDA": "Technology",
    "AVGO": "Technology",
    "ADBE": "Technology",
    "CRM": "Technology",
    "ORCL": "Technology",
    "CSCO": "Technology",
    "AMD": "Technology",
    "MU": "Technology",
    "QCOM": "Technology",
    "TXN": "Technology",
    "PANW": "Technology",
    "INTC": "Technology",
    "AMAT": "Technology",
    "NOW": "Technology",
    "IBM": "Technology",
    "LRCX": "Technology",
    # Communication Services
    "GOOGL": "Communication Services",
    "GOOG": "Communication Services",
    "META": "Communication Services",
    "NFLX": "Communication Services",
    "DIS": "Communication Services",
    "CMCSA": "Communication Services",
    "TMUS": "Communication Services",
    "VZ": "Communication Services",
    "T": "Communication Services",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary",
    "NKE": "Consumer Discretionary",
    "UBER": "Consumer Discretionary",
    "ABNB": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary",
    "LOW": "Consumer Discretionary",
    "TJX": "Consumer Discretionary",
    "BKNG": "Consumer Discretionary",
    # Consumer Staples
    "KO": "Consumer Staples",
    "PEP": "Consumer Staples",
    "COST": "Consumer Staples",
    "WMT": "Consumer Staples",
    "PG": "Consumer Staples",
    "PM": "Consumer Staples",
    "MO": "Consumer Staples",
    "MDLZ": "Consumer Staples",
    "CL": "Consumer Staples",
    # Financials
    "JPM": "Financials",
    "BAC": "Financials",
    "V": "Financials",
    "MA": "Financials",
    "GS": "Financials",
    "MS": "Financials",
    "WFC": "Financials",
    "C": "Financials",
    "BLK": "Financials",
    "AXP": "Financials",
    "SPGI": "Financials",
    "CB": "Financials",
    "PGR": "Financials",
    # Health Care
    "UNH": "Health Care",
    "JNJ": "Health Care",
    "LLY": "Health Care",
    "PFE": "Health Care",
    "ISRG": "Health Care",
    "VRTX": "Health Care",
    "REGN": "Health Care",
    "ABBV": "Health Care",
    "MRK": "Health Care",
    "TMO": "Health Care",
    "ABT": "Health Care",
    "DHR": "Health Care",
    "BMY": "Health Care",
    # Energy
    "XOM": "Energy",
    "CVX": "Energy",
    "COP": "Energy",
    "EOG": "Energy",
    "SLB": "Energy",
    "MPC": "Energy",
    "PSX": "Energy",
    "VLO": "Energy",
    "OXY": "Energy",
    # Industrials
    "CAT": "Industrials",
    "DE": "Industrials",
    "GE": "Industrials",
    "UNP": "Industrials",
    "HON": "Industrials",
    "RTX": "Industrials",
    "BA": "Industrials",
    "LMT": "Industrials",
    "UPS": "Industrials",
    "FDX": "Industrials",
    # Utilities & Energy Transition
    "NEE": "Utilities",
    "SO": "Utilities",
    "DUK": "Utilities",
    "ENPH": "Technology",
    # Real Estate
    "O": "Real Estate",
    "PLD": "Real Estate",
    "AMT": "Real Estate",
    "EQIX": "Real Estate",
    "CCI": "Real Estate",
    # Materials
    "LIN": "Materials",
    "SHW": "Materials",
    "FCX": "Materials",
    "NEM": "Materials",
    # User watchlist additions
    "BE": "Industrials",  # Bloom Energy — fuel cells / power gen
    "VRT": "Technology",  # Vertiv — data-center power & cooling
    "NBIS": "Technology",  # Nebius — AI cloud infrastructure
    "APP": "Technology",  # AppLovin — ad-tech / mobile
    "RDDT": "Communication Services",  # Reddit — social media
    "HIMS": "Health Care",  # Hims & Hers — telehealth
    "SOFI": "Financials",  # SoFi — digital banking / fintech
    "ZETA": "Technology",  # Zeta Global — marketing data/cloud
    "TSM": "Technology",  # TSMC — semiconductor foundry (ADR)
    "PLTR": "Technology",  # Palantir — data analytics / AI software
    "MRNA": "Health Care",  # Moderna — biotech / mRNA therapeutics
    # Trending / NVDA-ecosystem additions
    "SMCI": "Technology",  # Super Micro — AI servers
    "DELL": "Technology",  # Dell — AI servers / infrastructure
    "ANET": "Technology",  # Arista — data-center networking
    "COHR": "Technology",  # Coherent — optical transceivers
    "ALAB": "Technology",  # Astera Labs — AI connectivity chips
    "CRDO": "Technology",  # Credo — high-speed connectivity
    "GLW": "Technology",  # Corning — optical fiber / data-center cabling
    "CRWD": "Technology",  # CrowdStrike — cybersecurity
    "ZS": "Technology",  # Zscaler — cloud security
    "NET": "Technology",  # Cloudflare — edge network
    "DDOG": "Technology",  # Datadog — observability
    "SNOW": "Technology",  # Snowflake — data cloud
    "MDB": "Technology",  # MongoDB — database platform
    "ARM": "Technology",  # Arm Holdings — chip IP
    "HOOD": "Financials",  # Robinhood — retail brokerage
    "COIN": "Financials",  # Coinbase — crypto exchange
    "MSTR": "Financials",  # Strategy (MicroStrategy) — bitcoin treasury
    "AFRM": "Financials",  # Affirm — BNPL fintech
    "PYPL": "Financials",  # PayPal — digital payments
    "SPOT": "Communication Services",  # Spotify — audio streaming
    "RBLX": "Communication Services",  # Roblox — gaming / UGC
    "PINS": "Communication Services",  # Pinterest — social discovery
    "SNAP": "Communication Services",  # Snap — social media
    "DASH": "Consumer Discretionary",  # DoorDash — delivery
    "SHOP": "Consumer Discretionary",  # Shopify — e-commerce
    "RKLB": "Industrials",  # Rocket Lab — space launch
    "ASTS": "Technology",  # AST SpaceMobile — satellite direct-to-cell
    "TEM": "Health Care",  # Tempus AI — health data / AI diagnostics
    "SOUN": "Technology",  # SoundHound — voice AI
    "SERV": "Technology",  # Serve Robotics — autonomous delivery
    "IONQ": "Technology",  # IonQ — quantum computing
    "RGTI": "Technology",  # Rigetti — quantum computing
    "VST": "Utilities",  # Vistra — power (data-center demand)
    "CEG": "Utilities",  # Constellation — nuclear power
    "TLN": "Utilities",  # Talen Energy — nuclear-adjacent data-center power
    "OKLO": "Utilities",  # Oklo — advanced nuclear
}


def get_ticker_sector(ticker: str) -> str:
    """Return the sector name for a ticker, defaulting to 'Other' if unknown."""
    return SECTOR_MAP.get(ticker.upper(), "Other")


def get_skew_universe(tier: str = "core") -> list[str]:
    """Return tickers for the options skew pipeline.

    - "core": ~50 liquid large-caps + sector ETFs + market baselines (fast, robust for daily scan)
    - "sp500": S&P 500 constituents + sector ETFs
    """
    tickers: list[str] = list(MARKET_ETFS) + list(SECTOR_ETFS.keys())

    if tier.lower() == "core":
        for t in DEFAULT_UNIVERSE:
            if t not in tickers:
                tickers.append(t)
    elif tier.lower() == "sp500":
        sp_symbols = fetch_sp500_symbols()
        for t in sp_symbols:
            if t not in tickers:
                tickers.append(t)
    else:
        # custom comma-separated or fallback to core
        for t in DEFAULT_UNIVERSE:
            if t not in tickers:
                tickers.append(t)

    return tickers
