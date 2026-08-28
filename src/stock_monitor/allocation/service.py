"""Bridge from stored state to the allocation engine.

Maps what we know about the portfolio (open positions + the latest scan scores +
feature volatilities + sentiment archives) into the engine's ``PositionInput``
contracts, runs ``allocate``, and returns a JSON-friendly snapshot.

Design rules (approved 2026-08-27): the engine's numbers are deterministic and
auditable; sentiment is optional tilt; anything missing simply means no tilt.
"""

from __future__ import annotations

import datetime as dt

from stock_monitor.allocation.contracts import (
    AllocationConstraints,
    AllocationPlan,
    PortfolioState,
    PositionInput,
)
from stock_monitor.allocation.engine import allocate
from stock_monitor.storage.db import Storage


def _latest_features(store: Storage) -> dict[str, dict[str, float]]:
    """Newest feature row per ticker (as_of descending wins)."""
    frame = store.read_features()
    out: dict[str, dict[str, float]] = {}
    if frame.empty:
        return out
    frame = frame.sort_values("as_of")
    for _, row in frame.iterrows():
        out[str(row["ticker"])] = {
            c: float(row[c])
            for c in ("vol_3m", "sentiment")
            if c in frame.columns and row.get(c) is not None
        }
    return out


def _latest_news_sentiment(store: Storage) -> dict[str, float]:
    frame = store.read_news_sentiment()
    out: dict[str, float] = {}
    if frame.empty:
        return out
    latest = frame.sort_values("date").groupby("ticker").tail(1)
    for _, row in latest.iterrows():
        if row.get("sentiment") is not None:
            out[str(row["ticker"])] = float(row["sentiment"])
    return out


def _latest_alt_sentiment(store: Storage) -> dict[str, float]:
    frame = store.read_alt_sentiment()
    out: dict[str, float] = {}
    if frame.empty:
        return out
    latest = frame.sort_values("date").groupby("ticker").tail(1)
    for _, row in latest.iterrows():
        if row.get("sentiment") is not None:
            out[str(row["ticker"])] = float(row["sentiment"])
    return out


def _open_position_values(
    store: Storage,
    price_provider: object,
) -> tuple[dict[str, float], dict[str, str]]:
    """Current market value per open tracked position + any pricing errors.

    Value = last known price (live quote, else last cached close). Positions we
    cannot price are skipped from the weighting but reported.
    """
    values: dict[str, float] = {}
    errors: dict[str, str] = {}
    for position in store.list_positions():
        if position["status"] != "open":
            continue
        ticker = position["ticker"]
        price: float | None = None
        try:
            quote = price_provider.get_quote(ticker)  # type: ignore[attr-defined]
            if quote is not None and float(quote) > 0:
                price = float(quote)
        except Exception:  # noqa: BLE001 — quote is optional; fall back to bars
            price = None
        if price is None:
            try:
                frame = price_provider.get_prices(  # type: ignore[attr-defined]
                    ticker,
                    dt.date.today() - dt.timedelta(days=14),
                    dt.date.today(),
                )
                if frame is not None and not frame.empty and "close" in frame:
                    last = float(frame["close"].iloc[-1])
                    if last > 0:
                        price = last
            except Exception:  # noqa: BLE001 — unpriceable just drops from the plan
                price = None
        if price is None:
            errors[ticker] = "no price"
            continue
        values[ticker] = price
    return values, errors


def build_allocation_plan(
    store: Storage,
    price_provider: object,
    total_value: float | None = None,
    constraints: AllocationConstraints | None = None,
    restrict_tickers: list[str] | None = None,
) -> tuple[AllocationPlan, dict[str, object]]:
    """Run the engine over open positions + recent scan candidates.

    Universe: open tracked positions (must be priced) ∪ recent scores with
    conviction ≥ the sell band. With ``restrict_tickers`` (the basket-builder
    "suggest split" flow), the universe is exactly those tickers instead —
    stored scores/features/sentiment apply where present, neutral defaults
    otherwise (never invented conviction; the UI shows the caveat).
    Total value defaults to the current open book value; if the book is empty,
    ``total_value`` is the hypothetical budget.
    """
    from stock_monitor.allocation.engine import SELL_BELOW

    features = _latest_features(store)
    news_sent = _latest_news_sentiment(store)
    alt_sent = _latest_alt_sentiment(store)

    prices, price_errors = _open_position_values(store, price_provider)

    # Current book: open positions priced at last close (weight = value / book).
    current_weights: list[tuple[str, float]] = []
    book_value = sum(prices.values())
    for ticker in prices:
        current_weights.append((ticker, prices[ticker] / book_value if book_value else 0.0))

    value = total_value if total_value is not None else book_value

    inputs: list[PositionInput] = []
    seen: set[str] = set()

    def add_input(ticker: str, conviction: float, risk_flags: list[str]) -> None:
        ticker = ticker.upper()
        if ticker in seen:
            return
        seen.add(ticker)
        vol = features.get(ticker, {}).get("vol_3m")
        inputs.append(
            PositionInput(
                ticker=ticker,
                conviction=float(conviction),
                volatility=float(vol) if vol is not None and vol > 0 else 0.30,
                news_sentiment=news_sent.get(ticker),
                alt_sentiment=alt_sent.get(ticker),
                risk_flags=tuple(risk_flags),
            )
        )

    if restrict_tickers:
        wanted = [t.strip().upper() for t in restrict_tickers if t.strip()]
        scores = {s["ticker"]: s for s in store.read_recent_scores(within_days=7)}
        unscored: list[str] = []
        for ticker in wanted:
            score = scores.get(ticker)
            if score is not None:
                add_input(ticker, score["conviction"], score["risk_flags"])
            else:
                # Neutral placeholder: the engine splits evenly between unknowns.
                unscored.append(ticker)
                add_input(ticker, 50.0, [])
        inputs = [i for i in inputs if i.ticker in set(wanted)]
    else:
        unscored = []
        for view in store.list_positions():
            if view["status"] != "open" or view["ticker"] not in prices:
                continue
            ticker = view["ticker"]
            recent = store.read_recent_scores(within_days=7)
            score = next((s for s in recent if s["ticker"] == ticker), None)
            add_input(
                ticker,
                score["conviction"] if score else view["entry_conviction"],
                score["risk_flags"] if score else [],
            )

        for score in store.read_recent_scores(within_days=3):
            if score["conviction"] >= SELL_BELOW:
                add_input(score["ticker"], score["conviction"], score["risk_flags"])

    plan = allocate(
        inputs,
        PortfolioState(total_value=value, positions=tuple(current_weights)),
        constraints or AllocationConstraints(),
    )
    diagnostics: dict[str, object] = {
        "book_value": round(book_value, 2),
        "total_value": round(value, 2),
        "price_errors": price_errors,
        "candidate_count": len(inputs),
        "restricted": bool(restrict_tickers),
        "unscored": unscored,
    }
    return plan, diagnostics


def plan_to_json(plan: AllocationPlan, diagnostics: dict[str, object]) -> dict[str, object]:
    """Serialize an AllocationPlan for the API/UI (percentages as 0-100)."""
    return {
        "as_of": plan.as_of.isoformat(),
        "total_value": plan.total_value,
        "allocations": [
            {
                "ticker": a.ticker,
                "target_pct": round(a.target_weight * 100, 2),
                "current_pct": round(a.current_weight * 100, 2),
                "delta_pct": round(a.delta_weight * 100, 2),
                "conviction": a.conviction,
                "reasons": list(a.reasons),
            }
            for a in plan.allocations
        ],
        "cash_pct": round(plan.cash_weight * 100, 2),
        "warnings": list(plan.warnings),
        "constraints": {
            "max_per_position_pct": plan.constraints.max_per_position * 100,
            "min_per_position_pct": plan.constraints.min_per_position * 100,
            "max_positions": plan.constraints.max_positions,
            "cash_floor_pct": plan.constraints.cash_floor * 100,
        },
        "diagnostics": diagnostics,
        "generated_on": dt.date.today().isoformat(),
    }
