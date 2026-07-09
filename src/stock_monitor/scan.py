"""Universe scan: score every ticker, cap by risk, rank, and persist (build-plan §7).

This is the engine behind the "top-N to buy now" list. It scores each name in the
universe with the *same* calibrated model + SHAP transparency the on-demand lookup
uses, applies hard risk-flag caps, ranks by the capped conviction, and stores the
ranking so the API can serve it instantly. Collectors degrade gracefully — one bad
ticker never sinks the whole scan.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.earnings import EarningsProvider, days_until_earnings
from stock_monitor.features.builder import build_feature_row
from stock_monitor.features.schema import validate_features
from stock_monitor.models.scorer import Scoreable, recommendation_band, score_row
from stock_monitor.providers.base import FundamentalProvider, PriceProvider
from stock_monitor.service import apply_risk_caps, risk_flags
from stock_monitor.storage.db import Storage

HISTORY_YEARS = 8


def _score_one(
    ticker: str,
    model: Scoreable,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    start: dt.date,
    end: dt.date,
    earnings_provider: EarningsProvider | None = None,
) -> dict | None:
    prices = price_provider.get_prices(ticker, start, end)
    if prices.empty:
        return None
    facts = fundamental_provider.get_fundamentals(ticker)
    as_of = prices.index[-1].date()
    row = build_feature_row(ticker, prices, facts, as_of)
    if row is None:
        return None

    valid, _, _ = validate_features(pd.DataFrame([row]))
    if valid.empty:
        return None

    result = score_row(model, row)
    price = float(prices["close"].iloc[-1])
    days = (
        days_until_earnings(earnings_provider, ticker, end)
        if earnings_provider is not None
        else None
    )
    capped, caps = apply_risk_caps(result.conviction, row, price, days)
    flags = risk_flags(row) + caps
    return {
        "ticker": ticker,
        "conviction": result.conviction,
        "capped_conviction": capped,
        "recommendation": recommendation_band(capped),
        "as_of": as_of,
        "risk_flags": flags,
        "calibrated": result.calibrated,
    }


def run_scan(
    universe: list[str],
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage | None = None,
    today: dt.date | None = None,
    earnings_provider: EarningsProvider | None = None,
) -> list[dict]:
    """Score and rank the universe; persist the ranking if storage is provided."""
    end = today or dt.date.today()
    start = end - dt.timedelta(days=365 * HISTORY_YEARS)

    results: list[dict] = []
    for ticker in universe:
        try:
            scored = _score_one(
                ticker, model, price_provider, fundamental_provider, start, end,
                earnings_provider,
            )
        except Exception:  # noqa: BLE001 — one bad ticker must not sink the scan
            scored = None
        if scored is not None:
            results.append(scored)

    results.sort(key=lambda r: r["capped_conviction"], reverse=True)
    for i, row in enumerate(results, start=1):
        row["rank"] = i
        row["model_version"] = model_version

    if storage is not None:
        storage.save_opportunities(dt.datetime.now(), results)

    return results


def high_conviction_entrants(
    previous: list[dict], current: list[dict], threshold: int
) -> list[dict]:
    """Return names that newly crossed the high-conviction threshold this scan.

    Only *new* entrants alert, so you get pinged when something enters the buy zone,
    not every scan for names that were already there (debounced by construction).
    """
    prev_hi = {
        o["ticker"] for o in previous if o.get("capped_conviction", 0) >= threshold
    }
    return [
        o
        for o in current
        if o["capped_conviction"] >= threshold and o["ticker"] not in prev_hi
    ]


def scan_job(
    settings: object,
    notifier: object | None = None,
    model: Scoreable | None = None,
    price_provider: PriceProvider | None = None,
    fundamental_provider: FundamentalProvider | None = None,
    universe: list[str] | None = None,
    job_name: str = "universe_scan",
    persist_opportunities: bool = True,
) -> list[dict]:
    """Run a scan: score → alert on new high-conviction → persist + heartbeat.

    Providers/model/notifier/universe are injectable for tests and for tiered
    scheduling (daily universe vs hourly watchlist); production loads them from
    ``settings``. Scoring runs without a DB lock; only the brief save/heartbeat write
    holds the DuckDB file, so a running API isn't blocked.

    ``persist_opportunities`` controls whether this scan *replaces* the stored ranking
    (the "ranked opportunities" page + paper-pick source). The full universe scan owns
    that list; the hourly watchlist scan sets this ``False`` so its handful of names
    still fires intraday alerts without wiping the full ranking.
    """
    from stock_monitor.earnings import get_earnings_provider
    from stock_monitor.models.registry import compute_model_version, load_model
    from stock_monitor.notify import get_notifier
    from stock_monitor.providers.edgar_provider import EdgarProvider
    from stock_monitor.providers.yfinance_provider import YFinanceProvider
    from stock_monitor.universe import get_scan_universe

    if model is None:
        model = load_model(settings.model_path)  # type: ignore[attr-defined]
    if model is None:
        raise RuntimeError("no trained model — run `stock-monitor-train` first")

    price_provider = price_provider or YFinanceProvider()
    fundamental_provider = fundamental_provider or EdgarProvider()
    notifier = notifier or get_notifier(settings)  # type: ignore[arg-type]
    threshold = settings.alert_conviction_threshold  # type: ignore[attr-defined]
    tickers = universe if universe is not None else get_scan_universe(settings)

    started = dt.datetime.now()
    ranked = run_scan(
        tickers,
        model,
        compute_model_version(model),
        price_provider,
        fundamental_provider,
        storage=None,
        earnings_provider=get_earnings_provider(settings),  # type: ignore[arg-type]
    )

    entrants: list[dict] = []
    with Storage(settings.db_path) as storage:  # type: ignore[attr-defined]
        previous = storage.read_latest_opportunities(limit=1000)
        entrants = high_conviction_entrants(previous, ranked, threshold)
        finished = dt.datetime.now()
        if persist_opportunities:
            storage.save_opportunities(finished, ranked)
        storage.record_run(job_name, "ok", f"{len(ranked)} scored", started, finished)

    if entrants:
        body = "\n".join(
            f"#{o['rank']} {o['ticker']} {o['capped_conviction']}/100 — {o['recommendation']}"
            for o in entrants
        )
        notifier.send(f"{len(entrants)} new high-conviction name(s)", body)  # type: ignore[attr-defined]
        from stock_monitor.metrics import ALERTS_SENT

        ALERTS_SENT.labels(kind="high_conviction").inc(len(entrants))

    return ranked


def main(argv: list[str] | None = None) -> int:
    import argparse

    from stock_monitor.config import get_settings

    parser = argparse.ArgumentParser(description="Stock-Monitor universe scan")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)

    settings = get_settings()
    print("Scanning universe ...")
    try:
        ranked = scan_job(settings)
    except RuntimeError as exc:
        print(exc)
        return 1

    print(f"\nTop {args.top} of {len(ranked)} scanned (capped conviction):")
    print("-" * 60)
    for row in ranked[: args.top]:
        flags = ", ".join(row["risk_flags"]) or "-"
        print(
            f"  #{row['rank']:<2} {row['ticker']:<6} {row['capped_conviction']:>3}/100 "
            f"{row['recommendation']:<26} [{flags}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
