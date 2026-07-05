"""Paper mode: score the engine's daily 'buy' call against the benchmark, no real money.

Every daily scan, the top names in the buy zone are logged as *simulated* buys with the
price they'd have paid and the benchmark's price that day. Once a pick's horizon matures,
it's marked closed with its realised return vs the benchmark. The running hit-rate and
average excess return are the honest answer to "does this thing actually work?" — the
validation you want *before* trusting it with real money (build-plan Phase 4).

Guardrail: this is bookkeeping only. Nothing here places a trade — you always execute.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging

import pandas as pd

from stock_monitor.config import Settings
from stock_monitor.providers.base import PriceProvider
from stock_monitor.storage.db import Storage

logger = logging.getLogger("stock_monitor.paper")

BENCHMARK = "SPY"
_LOOKBACK_DAYS = 15  # small window to resolve a close price around a target date


def _add_months(d: dt.date, months: int) -> dt.date:
    """Return ``d`` shifted forward by ``months`` (clamped to a valid day-of-month)."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def _close_on_or_before(prices: pd.DataFrame, date: dt.date) -> float | None:
    """Last available close on or before ``date`` (handles weekends/holidays)."""
    if prices.empty or "close" not in prices:
        return None
    mask = pd.Index(prices.index).date <= date
    subset = prices.loc[mask]
    if subset.empty:
        return None
    return float(subset["close"].iloc[-1])


def record_paper_picks(
    settings: Settings,
    ranked: list[dict],
    price_provider: PriceProvider,
    storage: Storage,
    *,
    today: dt.date | None = None,
    benchmark: str = BENCHMARK,
) -> int:
    """Log today's buy-zone names as simulated buys. Idempotent per (ticker, day, horizon).

    Returns the number of *new* paper picks recorded.
    """
    pick_date = today or dt.date.today()
    horizon = settings.paper_horizon_months
    matured_on = _add_months(pick_date, horizon)

    candidates = [
        r for r in ranked
        if int(r.get("capped_conviction", 0)) >= settings.paper_min_conviction
    ]
    if not candidates:
        return 0

    start = pick_date - dt.timedelta(days=_LOOKBACK_DAYS)
    bench_prices = price_provider.get_prices(benchmark, start, pick_date)
    benchmark_entry = _close_on_or_before(bench_prices, pick_date)
    if benchmark_entry is None:
        logger.warning("paper: no benchmark price for %s on %s", benchmark, pick_date)
        return 0

    recorded = 0
    for row in candidates:
        ticker = row["ticker"]
        prices = price_provider.get_prices(ticker, start, pick_date)
        entry_price = _close_on_or_before(prices, pick_date)
        if entry_price is None or entry_price <= 0:
            continue
        pick_id = f"{ticker}:{pick_date.isoformat()}:{horizon}"
        if storage.record_paper_pick(
            pick_id=pick_id,
            ticker=ticker,
            pick_date=pick_date,
            conviction=int(row.get("capped_conviction", 0)),
            recommendation=row.get("recommendation", ""),
            horizon_months=horizon,
            entry_price=entry_price,
            benchmark_entry=benchmark_entry,
            model_version=row.get("model_version", "unknown"),
            matured_on=matured_on,
        ):
            recorded += 1
    return recorded


def evaluate_paper_picks(
    settings: Settings,
    price_provider: PriceProvider,
    storage: Storage,
    *,
    today: dt.date | None = None,
    benchmark: str = BENCHMARK,
) -> int:
    """Close every open pick whose horizon has matured, scoring it vs the benchmark.

    Returns the number of picks closed this run.
    """
    as_of = today or dt.date.today()
    open_picks = [
        p for p in storage.list_paper_picks(status="open")
        if p["matured_on"] is not None
        and dt.date.fromisoformat(p["matured_on"]) <= as_of
    ]
    if not open_picks:
        return 0

    start = as_of - dt.timedelta(days=_LOOKBACK_DAYS)
    bench_prices = price_provider.get_prices(benchmark, start, as_of)

    closed = 0
    for pick in open_picks:
        matured = dt.date.fromisoformat(pick["matured_on"])
        prices = price_provider.get_prices(pick["ticker"], start, as_of)
        exit_price = _close_on_or_before(prices, matured)
        benchmark_exit = _close_on_or_before(bench_prices, matured)
        if exit_price is None or benchmark_exit is None:
            continue
        entry = pick["entry_price"]
        bench_entry = pick["benchmark_entry"]
        if not entry or not bench_entry:
            continue
        stock_return = exit_price / entry - 1.0
        benchmark_return = benchmark_exit / bench_entry - 1.0
        excess = stock_return - benchmark_return
        storage.close_paper_pick(
            pick["id"],
            exit_price=exit_price,
            benchmark_exit=benchmark_exit,
            stock_return=stock_return,
            benchmark_return=benchmark_return,
            excess_return=excess,
            beat_benchmark=excess > 0,
        )
        closed += 1
    return closed


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def paper_summary(storage: Storage) -> dict:
    """Return the paper-mode track record: hit-rate + average excess return vs benchmark."""
    picks = storage.list_paper_picks()
    closed = [p for p in picks if p["status"] == "closed"]
    open_picks = [p for p in picks if p["status"] == "open"]

    beats = [p for p in closed if p["beat_benchmark"]]
    hit_rate = len(beats) / len(closed) if closed else None
    avg_excess = _mean([p["excess_return"] for p in closed if p["excess_return"] is not None])
    avg_conv_hit = _mean([p["conviction"] for p in beats])
    avg_conv_miss = _mean([p["conviction"] for p in closed if not p["beat_benchmark"]])

    by_excess = sorted(
        (p for p in closed if p["excess_return"] is not None),
        key=lambda p: p["excess_return"],
    )
    return {
        "total_picks": len(picks),
        "open": len(open_picks),
        "closed": len(closed),
        "hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
        "avg_excess_return": round(avg_excess, 4) if avg_excess is not None else None,
        "avg_conviction_hits": round(avg_conv_hit, 1) if avg_conv_hit is not None else None,
        "avg_conviction_misses": round(avg_conv_miss, 1) if avg_conv_miss is not None else None,
        "best": by_excess[-1] if by_excess else None,
        "worst": by_excess[0] if by_excess else None,
        "recent_open": open_picks[:10],
    }


def compose_digest(ranked: list[dict], summary: dict | None, top_n: int = 10) -> tuple[str, str]:
    """Build a (title, body) digest of the top names + paper track record."""
    top = ranked[:top_n]
    title = f"Stock-Monitor digest — top {len(top)} names"
    lines: list[str] = []
    for row in top:
        flags = row.get("risk_flags") or []
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"#{row.get('rank', '?')} {row['ticker']} "
            f"{row.get('capped_conviction', row.get('conviction'))}/100 "
            f"— {row.get('recommendation', '')}{flag_str}"
        )
    if not top:
        lines.append("No ranked names in this scan.")

    if summary and summary.get("closed"):
        hit = summary["hit_rate"]
        exc = summary["avg_excess_return"]
        lines.append("")
        lines.append(
            f"Paper track record: {summary['closed']} closed, "
            f"hit-rate {hit:.0%} vs SPY, "
            f"avg excess {exc:+.1%} (open: {summary['open']})."
        )

    lines.append("")
    lines.append("Decision-support only — you execute every trade. No auto-trading.")
    return title, "\n".join(lines)
