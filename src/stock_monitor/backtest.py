"""Leakage-aware backtest with transaction costs (build-plan §1.5, §7 Phase 2).

A backtest with no costs and hindsight is a fantasy that always looks amazing and
never survives contact with reality. This one is deliberately honest:

- **Walk-forward**: at each monthly rebalance the model is retrained only on rows
  whose label window already closed (purged by the ``embargo``), so it never trades
  on information it couldn't have had.
- **Costs + slippage**: a per-unit-turnover cost (bps) is charged every rebalance,
  so churn is penalised the way it is in a real account.
- **Honest reporting**: total return and CAGR vs SPY, max drawdown, hit-rate
  (fraction of periods that beat the benchmark), and average turnover.

Strategy: each month, rank the watchlist by the model's probability of beating SPY,
hold an equal-weight basket of the top-k names for one month, repeat.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_monitor.features.builder import (
    FEATURE_COLUMNS,
    _price_on_or_after,
    _price_on_or_before,
    build_training_frame,
)
from stock_monitor.models.scorer import LABEL_WINDOW_MONTHS, train_model
from stock_monitor.providers.base import FundamentalProvider, PriceProvider

BENCHMARK = "SPY"
HISTORY_YEARS = 8


@dataclass(frozen=True)
class BacktestResult:
    n_periods: int
    strategy_total_return: float
    benchmark_total_return: float
    excess_return: float
    strategy_cagr: float
    benchmark_cagr: float
    max_drawdown: float
    hit_rate: float  # fraction of rebalances that beat the benchmark
    avg_turnover: float
    cost_bps: float
    equity_curve: list[tuple[str, float]]


def run_backtest(
    frame: pd.DataFrame,
    price_frames: dict[str, pd.DataFrame],
    benchmark_prices: pd.DataFrame,
    top_k: int = 3,
    cost_bps: float = 10.0,
    embargo_months: int = LABEL_WINDOW_MONTHS,
    min_train: int = 24,
) -> BacktestResult:
    """Run a purged walk-forward, cost-aware monthly-rebalance backtest."""
    frame = frame.reset_index(drop=True).copy()
    frame["as_of_ts"] = pd.to_datetime(frame["as_of"])
    dates = sorted(frame["as_of_ts"].unique())

    equity = 1.0
    bench_equity = 1.0
    peak = 1.0
    max_dd = 0.0
    wins = 0
    periods = 0
    turnovers: list[float] = []
    prev_weights: dict[str, float] = {}
    curve: list[tuple[str, float]] = []

    for i in range(len(dates) - 1):
        t = pd.Timestamp(dates[i])
        nxt = pd.Timestamp(dates[i + 1])

        train = frame[frame["as_of_ts"] <= t - pd.DateOffset(months=embargo_months)]
        if len(train) < min_train or train["label"].nunique() < 2:
            continue

        today = frame[frame["as_of_ts"] == t]
        if today.empty:
            continue

        model = train_model(train)
        scores = model.predict_proba(today[list(FEATURE_COLUMNS)])[:, 1]
        ranked = today.assign(_score=scores).sort_values("_score", ascending=False)
        selected = ranked.head(top_k)["ticker"].tolist()
        if not selected:
            continue

        # Realised equal-weight return of the held basket over the holding month.
        rets: list[float] = []
        for ticker in selected:
            prices = price_frames.get(ticker)
            if prices is None:
                continue
            p0 = _price_on_or_before(prices, t)
            p1 = _price_on_or_after(prices, nxt)
            if p0 and p1 and p0 > 0:
                rets.append(p1 / p0 - 1.0)
        if not rets:
            continue
        gross_return = float(np.mean(rets))

        weight = 1.0 / len(selected)
        new_weights = {ticker: weight for ticker in selected}
        turnover = sum(
            abs(new_weights.get(tk, 0.0) - prev_weights.get(tk, 0.0))
            for tk in set(new_weights) | set(prev_weights)
        )
        cost = (cost_bps / 1e4) * turnover
        net_return = gross_return - cost

        b0 = _price_on_or_before(benchmark_prices, t)
        b1 = _price_on_or_after(benchmark_prices, nxt)
        bench_return = (b1 / b0 - 1.0) if (b0 and b1 and b0 > 0) else 0.0

        equity *= 1.0 + net_return
        bench_equity *= 1.0 + bench_return
        prev_weights = new_weights
        turnovers.append(turnover)
        if net_return > bench_return:
            wins += 1
        periods += 1

        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
        curve.append((nxt.date().isoformat(), equity))

    if periods == 0:
        raise RuntimeError(
            "Backtest produced no tradeable periods — need more history/tickers "
            "(each period must train on both classes after the embargo)."
        )

    years = periods / 12.0
    return BacktestResult(
        n_periods=periods,
        strategy_total_return=equity - 1.0,
        benchmark_total_return=bench_equity - 1.0,
        excess_return=(equity - 1.0) - (bench_equity - 1.0),
        strategy_cagr=equity ** (1.0 / years) - 1.0 if years > 0 else 0.0,
        benchmark_cagr=bench_equity ** (1.0 / years) - 1.0 if years > 0 else 0.0,
        max_drawdown=max_dd,
        hit_rate=wins / periods,
        avg_turnover=float(np.mean(turnovers)) if turnovers else 0.0,
        cost_bps=cost_bps,
        equity_curve=curve,
    )


def _fetch(
    watchlist: list[str],
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    label_window_months: int,
    macro_lookup: Callable[[dt.date], dict[str, float]] | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * HISTORY_YEARS)

    benchmark = price_provider.get_prices(BENCHMARK, start, end)
    if benchmark.empty:
        raise RuntimeError(f"benchmark {BENCHMARK} price history unavailable")

    price_frames: dict[str, pd.DataFrame] = {}
    frames: list[pd.DataFrame] = []
    for ticker in watchlist:
        prices = price_provider.get_prices(ticker, start, end)
        if prices.empty:
            continue
        price_frames[ticker] = prices
        facts = fundamental_provider.get_fundamentals(ticker)
        frame = build_training_frame(
            ticker, prices, facts, benchmark, label_window_months,
            macro_lookup=macro_lookup,
        )
        if not frame.empty:
            frames.append(frame)

    if not frames:
        raise RuntimeError("no labelled data could be assembled for the backtest")
    return pd.concat(frames, ignore_index=True), price_frames, benchmark


def _format_report(result: BacktestResult, top_k: int) -> str:
    return "\n".join(
        [
            "=" * 70,
            "BACKTEST (purged walk-forward, monthly rebalance) — Phase 2 trust check",
            "=" * 70,
            f"periods (months)   : {result.n_periods}   top-{top_k} equal-weight, "
            f"cost {result.cost_bps:.0f} bps/turnover",
            f"strategy total ret : {result.strategy_total_return:+.2%}   "
            f"CAGR {result.strategy_cagr:+.2%}",
            f"benchmark (SPY)    : {result.benchmark_total_return:+.2%}   "
            f"CAGR {result.benchmark_cagr:+.2%}",
            f"excess vs SPY      : {result.excess_return:+.2%}",
            f"max drawdown       : {result.max_drawdown:.2%}",
            f"hit-rate vs SPY    : {result.hit_rate:.2%}   "
            f"avg turnover {result.avg_turnover:.2f}",
            "",
            "Note: small watchlist + short history = noisy, illustrative numbers. "
            "Trust the machinery, not the magnitude, until the universe widens (Phase 3).",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    from stock_monitor.config import get_settings
    from stock_monitor.pipeline import DEFAULT_WATCHLIST
    from stock_monitor.providers.edgar_provider import EdgarProvider
    from stock_monitor.providers.yfinance_provider import YFinanceProvider

    parser = argparse.ArgumentParser(description="Stock-Monitor walk-forward backtest")
    parser.add_argument("-w", "--watchlist", nargs="+", default=list(DEFAULT_WATCHLIST))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    args = parser.parse_args(argv)

    settings = get_settings()
    frame, price_frames, benchmark = _fetch(
        [t.upper() for t in args.watchlist],
        YFinanceProvider(),
        EdgarProvider(),
        settings.label_window_months,
    )
    result = run_backtest(
        frame,
        price_frames,
        benchmark,
        top_k=args.top_k,
        cost_bps=args.cost_bps,
        embargo_months=settings.label_window_months,
    )
    print(_format_report(result, args.top_k))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
