"""Data fetcher for options chains, spot prices, and returns via yfinance.

Extracts:
1. Target monthly options chain ~30-60 DTE.
2. Per-strike implied volatility (calls & puts).
3. 1-Month underlying return and 30-day relative volume (RVOL).
4. Earnings calendar date to flag Trap #5 ("Event Premium, Not Pure Sentiment").
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
import time
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class RawChainData:
    ticker: str
    spot: float
    ret_1m: float
    rvol: float
    expiration: str
    dte_days: int
    strikes: list[float]
    call_ivs: dict[float, float]
    put_ivs: dict[float, float]
    call_volumes: dict[float, float] = field(default_factory=dict)
    put_volumes: dict[float, float] = field(default_factory=dict)
    call_open_interests: dict[float, float] = field(default_factory=dict)
    put_open_interests: dict[float, float] = field(default_factory=dict)
    earnings_date: str | None = None
    is_earnings_near: bool = False
    error: str | None = None


def select_target_expiration(
    expirations: tuple[str, ...],
    as_of: dt.date,
    min_dte: int = 25,
    max_dte: int = 65,
    target_dte: int = 40,
) -> tuple[str, int] | None:
    """Select the best standard monthly or liquid expiration around 30-60 DTE."""
    if not expirations:
        return None

    candidates: list[tuple[str, int, bool]] = []
    for exp_str in expirations:
        try:
            exp_date = dt.datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - as_of).days
            if dte <= 0:
                continue
            # Check if 3rd Friday (day between 15 and 21, weekday == 4)
            is_monthly = (15 <= exp_date.day <= 21) and (exp_date.weekday() == 4)
            candidates.append((exp_str, dte, is_monthly))
        except ValueError:
            continue

    if not candidates:
        return None

    # Filter within [min_dte, max_dte]
    in_range = [c for c in candidates if min_dte <= c[1] <= max_dte]
    if in_range:
        # Prefer standard monthly if available
        monthlies = [c for c in in_range if c[2]]
        if monthlies:
            best = min(monthlies, key=lambda c: abs(c[1] - target_dte))
            return best[0], best[1]
        # Otherwise closest to target_dte
        best = min(in_range, key=lambda c: abs(c[1] - target_dte))
        return best[0], best[1]

    # If none in [min_dte, max_dte], pick closest to target_dte with DTE >= 14
    future = [c for c in candidates if c[1] >= 14]
    if future:
        best = min(future, key=lambda c: abs(c[1] - target_dte))
        return best[0], best[1]

    return None


def fetch_spy_1m_return(as_of: dt.date | None = None) -> float:
    """Fetch SPY 1-month return to serve as the market benchmark."""
    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(period="3mo")
        if not hist.empty:
            hist = hist.dropna(subset=["Close"])
        if len(hist) >= 21:
            close_now = float(hist["Close"].iloc[-1])
            close_1m = float(hist["Close"].iloc[-21])
            return (close_now - close_1m) / close_1m if close_1m > 0 else 0.0
    except Exception as exc:
        logger.warning("Failed to fetch SPY 1M return: %s", exc)
    return 0.0


def fetch_single_chain(
    ticker: str,
    as_of: dt.date | None = None,
    min_dte: int = 25,
    max_dte: int = 65,
    target_dte: int = 40,
) -> RawChainData:
    """Fetch spot, history, expiration, and chain data for a single ticker."""
    as_of_date = as_of or dt.date.today()
    try:
        t = yf.Ticker(ticker)

        # 1. Price history for 1M return & RVOL
        hist = t.history(period="3mo")
        if not hist.empty:
            hist = hist.dropna(subset=["Close"])
        if hist.empty or len(hist) < 5:
            return RawChainData(
                ticker=ticker,
                spot=0.0,
                ret_1m=0.0,
                rvol=1.0,
                expiration="",
                dte_days=0,
                strikes=[],
                call_ivs={},
                put_ivs={},
                error="Insufficient price history",
            )

        spot = float(hist["Close"].iloc[-1])
        if len(hist) >= 21:
            close_1m = float(hist["Close"].iloc[-21])
            ret_1m = (spot - close_1m) / close_1m if close_1m > 0 else 0.0
        else:
            close_first = float(hist["Close"].iloc[0])
            ret_1m = (spot - close_first) / close_first if close_first > 0 else 0.0

        # RVOL = latest volume / 30-day average volume
        vol_series = hist["Volume"].tail(30)
        avg_vol = float(vol_series.mean()) if not vol_series.empty else 1.0
        latest_vol = float(hist["Volume"].iloc[-1]) if not hist["Volume"].empty else 1.0
        rvol = (latest_vol / avg_vol) if avg_vol > 0 else 1.0

        # 2. Expiration selection
        expirations = t.options
        if not expirations:
            return RawChainData(
                ticker=ticker,
                spot=spot,
                ret_1m=ret_1m,
                rvol=rvol,
                expiration="",
                dte_days=0,
                strikes=[],
                call_ivs={},
                put_ivs={},
                error="No options expirations found",
            )

        target_exp = select_target_expiration(
            expirations,
            as_of_date,
            min_dte=min_dte,
            max_dte=max_dte,
            target_dte=target_dte,
        )
        if target_exp is None:
            return RawChainData(
                ticker=ticker,
                spot=spot,
                ret_1m=ret_1m,
                rvol=rvol,
                expiration="",
                dte_days=0,
                strikes=[],
                call_ivs={},
                put_ivs={},
                error="No suitable expiration found",
            )

        exp_str, dte = target_exp
        chain = t.option_chain(exp_str)

        call_ivs: dict[float, float] = {}
        put_ivs: dict[float, float] = {}
        call_vols: dict[float, float] = {}
        put_vols: dict[float, float] = {}
        call_ois: dict[float, float] = {}
        put_ois: dict[float, float] = {}
        all_strikes: set[float] = set()

        if chain.calls is not None and not chain.calls.empty:
            for _, row in chain.calls.iterrows():
                k = float(row["strike"])
                iv = float(row.get("impliedVolatility", 0.0))
                all_strikes.add(k)
                if iv > 0 and not pd.isna(iv):
                    call_ivs[k] = iv
                vol = row.get("volume", 0.0)
                if not pd.isna(vol):
                    call_vols[k] = float(vol)
                oi = row.get("openInterest", 0.0)
                if not pd.isna(oi):
                    call_ois[k] = float(oi)

        if chain.puts is not None and not chain.puts.empty:
            for _, row in chain.puts.iterrows():
                k = float(row["strike"])
                iv = float(row.get("impliedVolatility", 0.0))
                all_strikes.add(k)
                if iv > 0 and not pd.isna(iv):
                    put_ivs[k] = iv
                vol = row.get("volume", 0.0)
                if not pd.isna(vol):
                    put_vols[k] = float(vol)
                oi = row.get("openInterest", 0.0)
                if not pd.isna(oi):
                    put_ois[k] = float(oi)

        # 3. Check earnings date (Trap #5: Event Premium)
        earnings_date_str: str | None = None
        is_earnings_near = False
        try:
            cal = t.calendar
            if cal is not None and isinstance(cal, dict) and "Earnings Date" in cal:
                e_dates = cal["Earnings Date"]
                if e_dates:
                    first_ed = e_dates[0] if isinstance(e_dates, list) else e_dates
                    if hasattr(first_ed, "date"):
                        ed = first_ed.date()
                    elif isinstance(first_ed, dt.date):
                        ed = first_ed
                    else:
                        ed = pd.to_datetime(first_ed).date()
                    earnings_date_str = ed.strftime("%Y-%m-%d")
                    days_to_earnings = (ed - as_of_date).days
                    # If earnings falls between today and expiration + 5 days
                    if 0 <= days_to_earnings <= (dte + 5):
                        is_earnings_near = True
        except Exception:
            pass

        return RawChainData(
            ticker=ticker,
            spot=spot,
            ret_1m=ret_1m,
            rvol=rvol,
            expiration=exp_str,
            dte_days=dte,
            strikes=sorted(all_strikes),
            call_ivs=call_ivs,
            put_ivs=put_ivs,
            call_volumes=call_vols,
            put_volumes=put_vols,
            call_open_interests=call_ois,
            put_open_interests=put_ois,
            earnings_date=earnings_date_str,
            is_earnings_near=is_earnings_near,
        )

    except Exception as exc:
        logger.exception("Error fetching options chain for %s: %s", ticker, exc)
        return RawChainData(
            ticker=ticker,
            spot=0.0,
            ret_1m=0.0,
            rvol=1.0,
            expiration="",
            dte_days=0,
            strikes=[],
            call_ivs={},
            put_ivs={},
            error=str(exc),
        )


def fetch_universe_chains(
    tickers: list[str],
    as_of: dt.date | None = None,
    max_workers: int = 6,
    delay_between_batches: float = 0.2,
) -> list[RawChainData]:
    """Fetch chains for a list of tickers with controlled concurrency."""
    results: list[RawChainData] = []
    as_of_date = as_of or dt.date.today()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(fetch_single_chain, t, as_of_date): t
            for t in tickers
        }
        for future in concurrent.futures.as_completed(future_to_ticker):
            t = future_to_ticker[future]
            try:
                data = future.result()
                results.append(data)
            except Exception as exc:
                logger.error("Future failed for ticker %s: %s", t, exc)
            time.sleep(delay_between_batches)

    # Sort results by input ticker order
    order_map = {ticker: idx for idx, ticker in enumerate(tickers)}
    results.sort(key=lambda d: order_map.get(d.ticker, 999999))
    return results
