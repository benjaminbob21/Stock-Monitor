"""Short-horizon label construction (1-5 and 5-20 trading days).

The long-horizon training frame labels with a calendar-month forward window; the new
short-horizon model needs *trading-day* forward windows that match the 1-4 week
alert cadence. Labels here answer: *"could we have captured a beat-the-benchmark move
within this short window?"* — measured as the best forward exit inside the window.

Leakage rule (the same PIT contract as features):
- The decision price is the close on the trading bar at or *before* ``as_of``.
- Forward prices are taken *strictly after* that bar, so a decision never sees the
  same day's later print (e.g. an after-hours pop) as a known fact.

Both labels are hindsight targets, which is allowed — only features must stay PIT.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

# (window_start_trading_day, window_end_trading_day) after the decision bar.
SHORT_HORIZONS: tuple[tuple[int, int], ...] = (
    (1, 5),   # ~1 week: the event pop
    (5, 20),  # ~1 month: the follow-through
)

SHORT_LABEL_COLUMNS: tuple[str, ...] = (
    "label_1_5d",
    "label_5_20d",
    "fwd_ret_1_5d",
    "fwd_ret_5_20d",
    "benchmark_ret_1_5d",
    "benchmark_ret_5_20d",
)


def _bars_on_or_before(prices: pd.DataFrame, ts: pd.Timestamp) -> pd.Series:
    return prices.loc[:ts, "close"]


def _last_bar_on_or_before(prices: pd.DataFrame, ts: pd.Timestamp) -> pd.Timestamp | None:
    window = _bars_on_or_before(prices, ts)
    return window.index[-1] if not window.empty else None


def _bars_strictly_after(prices: pd.DataFrame, ts: pd.Timestamp) -> pd.Series:
    return prices.loc[prices.index > ts, "close"]


def _max_forward_return(closes: pd.Series, start_price: float) -> float:
    return float((closes / start_price - 1.0).max())


def build_short_horizon_labels(
    prices: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
    as_of: pd.Timestamp,
) -> dict[str, object] | None:
    """Compute short-horizon labels for a decision ``as_of``, or ``None`` if data is thin.

    Returns ``None`` when there is no decision bar at/before ``as_of`` or not enough
    forward trading days to form the 5-20 window.
    """
    if prices.empty or benchmark_prices.empty:
        return None

    price_bar = _last_bar_on_or_before(prices, as_of)
    benchmark_bar = _last_bar_on_or_before(benchmark_prices, as_of)
    if price_bar is None or benchmark_bar is None:
        return None

    p_now = float(prices.loc[price_bar, "close"])
    b_now = float(benchmark_prices.loc[benchmark_bar, "close"])
    if p_now == 0 or b_now == 0:
        return None

    price_future = _bars_strictly_after(prices, price_bar)
    benchmark_future = _bars_strictly_after(benchmark_prices, benchmark_bar)
    if len(price_future) < SHORT_HORIZONS[-1][1] or len(benchmark_future) < SHORT_HORIZONS[-1][1]:
        return None

    result: dict[str, object] = {"as_of": as_of}
    for start, end in SHORT_HORIZONS:
        suffix = f"{start}_{end}d"
        window = price_future.iloc[start - 1 : end]
        benchmark_window = benchmark_future.iloc[start - 1 : end]
        if window.empty or benchmark_window.empty:
            return None
        fwd_ret = _max_forward_return(window, p_now)
        bench_ret = _max_forward_return(benchmark_window, b_now)
        result[f"label_{suffix}"] = int(fwd_ret > bench_ret)
        result[f"fwd_ret_{suffix}"] = fwd_ret
        result[f"benchmark_ret_{suffix}"] = bench_ret
    return result


def build_short_horizon_training_rows(
    prices: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
    as_of_dates: Sequence[pd.Timestamp],
) -> pd.DataFrame:
    """Assemble a labelled short-horizon frame across ``as_of_dates``.

    Rows with insufficient forward history are dropped (near the end of the price
    series), matching how the monthly walk-forward frame drops incomplete rows.
    """
    rows: list[dict[str, object]] = []
    for as_of in as_of_dates:
        labels = build_short_horizon_labels(prices, benchmark_prices, pd.Timestamp(as_of))
        if labels is not None:
            rows.append(labels)
    return pd.DataFrame(rows)
