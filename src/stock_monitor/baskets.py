"""Joint portfolios ("baskets"): one budget split across stocks by percentage.

A basket is a single decision — "invest $10,000 like this: 40% NVDA, 30% MSFT,
30% SPY" — recorded once at creation (fractional shares from that day's prices)
and then valued as a whole. The headline view is *the entire capital*: current
value vs budget, P&L in $ and %, and how each constituent contributes to that
total (weight × its own return). Drilling into a leg shows its standalone
return; the two are related by ``contribution = weight × leg return``.

SPY is fetched as a same-budget benchmark so "how did my joint portfolio do"
always has an honest comparison next to it.

Prices come from the same provider position tracking uses (local cache with
yfinance upstream) — never the Tiingo free-tier hourly cap.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from stock_monitor.providers.base import PriceProvider
from stock_monitor.storage.db import Storage

BENCHMARK = "SPY"


class BasketError(ValueError):
    """User-input problem: weights must sum to ~100% and tickers must price."""


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return new / old - 1.0


def validate_weights(items: list[dict]) -> None:
    """Percentages must be positive and sum to 100 (within rounding slack)."""
    if not items:
        raise BasketError("a basket needs at least one stock")
    for item in items:
        pct = float(item.get("pct", 0))
        if not 0 < pct <= 100:
            raise BasketError("each percentage must be between 0 and 100")
    total = sum(float(i["pct"]) for i in items)
    if abs(total - 100.0) > 1.0:
        raise BasketError(f"percentages must sum to 100 (got {total:g}%)")


def create_basket(
    name: str,
    total_budget: float,
    tickers: list[str],
    pcts: list[float],
    price_provider: PriceProvider,
    storage: Storage,
    entry_convictions: dict[str, int] | None = None,
) -> dict:
    """Record a basket and snapshot fractional-share entries at current prices."""
    if total_budget <= 0:
        raise BasketError("total budget must be positive")
    entries: list[dict[str, Any]] = [
        {"ticker": str(t).upper().strip(), "pct": float(p)}
        for t, p in zip(tickers, pcts, strict=False)
    ]
    validate_weights(entries)

    now = dt.datetime.now()
    priced: list[dict] = []
    for item in entries:
        quote = price_provider.get_quote(item["ticker"])
        if not quote or quote <= 0:
            raise BasketError(f"could not price {item['ticker']} right now")
        budget = round(total_budget * item["pct"] / 100.0, 2)
        priced.append(
            {
                "id": str(uuid.uuid4()),
                "ticker": item["ticker"],
                "pct": item["pct"],
                "budget": budget,
                "entry_price": float(quote),
                # Fractional shares: the basket owns slices of each stock.
                "shares": round(budget / float(quote), 8),
                "entry_conviction": (entry_convictions or {}).get(item["ticker"]),
            }
        )

    basket_id = str(uuid.uuid4())
    storage.create_basket(basket_id, name, now, total_budget, priced)
    basket = storage.get_basket(basket_id)
    assert basket is not None  # just written
    return {**basket, "items": storage.list_basket_items(basket_id)}


def _quote_or_last_close(
    ticker: str, price_provider: PriceProvider, today: dt.date
) -> float | None:
    """Live quote when available; otherwise the last completed close (~5 days)."""
    quote = price_provider.get_quote(ticker)
    if quote:
        return float(quote)
    bars = price_provider.get_prices(
        ticker, today - dt.timedelta(days=10), today
    )
    if len(bars) == 0:
        return None
    closes = bars["close"] if hasattr(bars, "columns") else [b[-1] for b in bars]
    last = list(closes)[-1]
    return float(last) if last else None


def _basket_totals(
    basket: dict,
    items: list[dict],
    price_provider: PriceProvider,
) -> tuple[float, list[dict], bool]:
    """Return (current value, per-leg views, all legs fully valued)."""
    today = dt.date.today()
    total_shares_value = 0.0
    complete = True
    legs: list[dict] = []
    for item in items:
        exited_price = (
            item.get("sold_price") if item.get("status") == "sold" else None
        )
        current = exited_price or _quote_or_last_close(
            item["ticker"], price_provider, today
        )
        leg_return = _pct_change(current, item["entry_price"])
        value = (
            item["shares"] * current
            if current is not None
            else item["budget"]
        )
        if current is None:
            complete = False
        else:
            total_shares_value += float(value)
        contribution = (
            (float(value) - item["budget"]) / basket["total_budget"] * 100.0
            if current is not None
            else None
        )
        legs.append(
            {
                **item,
                "current_price": current,
                "leg_return_pct": (
                    round(leg_return * 100.0, 2) if leg_return is not None else None
                ),
                "current_value": round(float(value), 2),
                "pnl": round(float(value) - item["budget"], 2),
                # Contribution of this leg to the WHOLE basket's % move, in points.
                "contribution_points": round(contribution, 3) if contribution is not None else None,
            }
        )
    return total_shares_value, legs, complete


def basket_view(basket: dict, price_provider: PriceProvider) -> dict:
    """Value a basket as a whole, with per-stock contributions and a SPY read."""
    items = _items_of(basket)
    if not items:
        return {**basket, "legs": [], "complete": False}

    value, legs, complete = _basket_totals(basket, items, price_provider)
    pnl = value - basket["total_budget"]
    ret = _pct_change(value, basket["total_budget"])

    bench_ret = _benchmark_return(basket, price_provider)
    view: dict = {
        **basket,
        "current_value": round(value, 2),
        "pnl": round(pnl, 2),
        "return_pct": round(ret * 100.0, 2) if ret is not None else None,
        "benchmark_return_pct": (
            round(bench_ret * 100.0, 2) if bench_ret is not None else None
        ),
        "excess_vs_spy_pct": (
            round((ret - bench_ret) * 100.0, 2)
            if ret is not None and bench_ret is not None
            else None
        ),
        "complete": complete,
    }
    view["legs"] = sorted(legs, key=lambda leg: leg["pct"], reverse=True)
    return view


def _items_of(basket: dict) -> list[dict]:
    """Legs attached to the basket dict (summary mode returns nothing here)."""
    return list(basket.get("items") or [])


def _benchmark_return(basket: dict, price_provider: PriceProvider) -> float | None:
    """What the same budget in SPY would have done since the basket's creation."""
    created = basket.get("created_at")
    if not created:
        return None
    day = dt.date.fromisoformat(str(created)[:10])
    today = dt.date.today()
    bars = price_provider.get_prices(BENCHMARK, day - dt.timedelta(days=7), today)
    if len(bars) == 0:
        return None
    close_col = "close" if hasattr(bars, "columns") else None
    if close_col:
        first = float(list(bars[close_col])[0])
        last = float(list(bars[close_col])[-1])
    else:
        rows = list(bars)
        first, last = float(rows[0][-1]), float(rows[-1][-1])
    if not first:
        return None
    return last / first - 1.0


def sell_leg(item_id: str, price_provider: PriceProvider, storage: Storage) -> dict | None:
    """Mark one leg sold at the live price; recompute nothing here (views do)."""
    for basket in storage.list_baskets():
        items = storage.list_basket_items(basket["id"])
        match = next((i for i in items if i["id"] == item_id), None)
        if match is None:
            continue
        if match["status"] != "open":
            raise BasketError("leg already sold")
        quote = price_provider.get_quote(match["ticker"]) or match["entry_price"]
        storage.sell_basket_item(item_id, dt.datetime.now(), float(quote))
        return storage.get_basket(basket["id"])
    return None
