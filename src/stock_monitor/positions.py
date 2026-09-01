"""Position tracking + exit signals (build-plan §7 Phase 4).

You add a stock you bought; the tool snapshots what it thought the day you added it,
then re-scores it continuously and tells you whether to hold, trim, or sell — with
*two* independent reads so you can compare and decide for yourself:

- ``signal``      : a crisp, rule-based math verdict (conviction thresholds + flags).
- ``expert_view`` : a plain-language narrative (the "expert persona") synthesising
                    entry-vs-now conviction, the price move, drivers, and risk.

When you sell, we log the date + price and keep tracking so you can see, later,
whether selling was the right call (price-then vs price-now).
"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid

from stock_monitor.models.scorer import Scoreable
from stock_monitor.providers.base import FundamentalProvider, PriceProvider
from stock_monitor.sentiment import NewsProvider, SentimentAnalyzer, analyze_ticker
from stock_monitor.service import score_ticker
from stock_monitor.storage.db import Storage

# Exit thresholds on the calibrated conviction (mirrors the buy-side bands).
SELL_BELOW = 40
TRIM_BELOW = 55
_MATERIAL_FLAGS = {
    "negative_earnings",
    "extreme_volatility_cap",
    "penny_stock_cap",
    "negative_news",
}


def exit_signal(conviction: int, risk_flags: list[str]) -> str:
    """Rule-based hold/trim/sell verdict for a held position."""
    material = any(f in _MATERIAL_FLAGS for f in risk_flags)
    if conviction < SELL_BELOW or material:
        return "consider selling"
    if conviction < TRIM_BELOW:
        return "consider trimming / watch"
    return "hold"


def expert_view(
    entry_conviction: int,
    current_conviction: int,
    price_change: float | None,
    drivers: list[dict],
    signal: str,
) -> str:
    """A narrative 'expert' read to compare against the crisp math signal."""
    delta = current_conviction - entry_conviction
    trend = "risen" if delta > 0 else "fallen" if delta < 0 else "held steady"
    parts = [
        f"Conviction has {trend} from {entry_conviction} to {current_conviction} "
        "since you added it."
    ]
    if price_change is not None:
        parts.append(f"Price is {price_change:+.1%} vs your entry.")
    if drivers:
        top = drivers[0]
        parts.append(f"Biggest current driver: {top['feature']} ({top['direction']}).")
    closer = {
        "consider selling": "The edge has thinned — consider selling.",
        "consider trimming / watch": "Momentum is cooling — trim or watch closely.",
        "hold": "The thesis still holds — hold.",
    }[signal]
    parts.append(closer)
    return " ".join(parts)


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return new / old - 1.0


def _score_now(
    ticker: str,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage | None = None,
    short_model: Scoreable | None = None,
    earnings_provider: object | None = None,
) -> dict:
    return score_ticker(
        ticker,
        model=model,
        model_version=model_version,
        price_provider=price_provider,
        fundamental_provider=fundamental_provider,
        label_window_months=12,
        storage=storage,
        short_model=short_model,
        earnings_provider=earnings_provider,
    )


def open_position(
    ticker: str,
    *,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage,
    short_model: Scoreable | None = None,
    earnings_provider: object | None = None,
    quantity: float = 1.0,
) -> dict:
    """Snapshot today's price + score for ``ticker`` and start tracking it."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    _invalidate_view_cache()
    scored = _score_now(
        ticker,
        model,
        model_version,
        price_provider,
        fundamental_provider,
        storage,
        short_model,
        earnings_provider,
    )
    position_id = uuid.uuid4().hex[:12]
    storage.add_position(
        position_id=position_id,
        ticker=scored["ticker"],
        added_at=dt.datetime.now(),
        entry_price=float(scored["price"]),
        entry_conviction=int(scored["conviction"]),
        entry_recommendation=str(scored["recommendation"]),
        entry_drivers=list(scored["drivers"]),
        quantity=float(quantity),
    )
    stored = storage.get_position(position_id)
    assert stored is not None
    return position_view(
        stored,
        model,
        model_version,
        price_provider,
        fundamental_provider,
        storage,
        short_model=short_model,
        earnings_provider=earnings_provider,
    )


def _lot_summary(lots: list[dict], current_price: float | None) -> dict:
    """Aggregate a position's buy lots into blended-cost + per-lot P&L numbers."""
    total_qty = sum(lot["quantity"] for lot in lots)
    total_cost = sum(lot["quantity"] * lot["price"] for lot in lots)
    avg_price = total_cost / total_qty if total_qty else None
    per_lot = []
    for lot in lots:
        entry = {
            "bought_at": lot["bought_at"],
            "price": lot["price"],
            "quantity": lot["quantity"],
        }
        if note := lot.get("note"):
            entry["note"] = note
        if current_price is not None:
            entry["pnl_dollar"] = round(lot["quantity"] * (current_price - lot["price"]), 2)
            entry["pnl_pct"] = (
                round((current_price / lot["price"] - 1.0) * 100.0, 2)
                if lot["price"]
                else None
            )
        per_lot.append(entry)
    return {
        "lots": per_lot,
        "avg_entry_price": round(avg_price, 4) if avg_price is not None else None,
        "lot_count": len(lots),
    }


def add_to_position(
    position_id: str,
    *,
    quantity: float | None = None,
    dollars: float | None = None,
    price: float | None = None,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage,
    short_model: Scoreable | None = None,
    earnings_provider: object | None = None,
    note: str | None = None,
) -> dict:
    """Record an additional buy into an existing open position.

    The buy is snapshotted at today's live price (or an explicit ``price``),
    appended as a new lot, and the position's entry_price becomes the
    volume-weighted average across all lots. Entry conviction stays the
    original snapshot — it marks when the thesis was first formed.

    ``dollars`` sizes the buy in currency: shares = dollars / live price.
    Exactly one of ``quantity``/``dollars`` must be given.
    """
    if (quantity is not None) == (dollars is not None):
        raise ValueError("provide exactly one of quantity or dollars")
    if dollars is not None and dollars <= 0:
        raise ValueError("dollars must be positive")
    if quantity is not None and quantity <= 0:
        raise ValueError("quantity must be positive")
    position = storage.get_position(position_id)
    if position is None:
        raise KeyError(position_id)
    if position["status"] != "open":
        raise ValueError("cannot add to a sold position")
    _invalidate_view_cache()
    scored = _score_now(
        position["ticker"],
        model,
        model_version,
        price_provider,
        fundamental_provider,
        storage,
        short_model,
        earnings_provider,
    )
    if price is None:
        price = float(scored["price"])
    if dollars is not None:
        if price <= 0:
            raise ValueError("live price is not positive; cannot size a dollar buy")
        quantity = dollars / price
    updated = storage.add_lot(
        position_id=position_id,
        bought_at=dt.datetime.now(),
        price=float(price),
        quantity=float(quantity or 0.0),
        note=note,
    )
    return position_view(
        updated,
        model,
        model_version,
        price_provider,
        fundamental_provider,
        storage,
        short_model=short_model,
        earnings_provider=earnings_provider,
    )


def position_view(
    position: dict,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage | None = None,
    news_provider: NewsProvider | None = None,
    analyzer: SentimentAnalyzer | None = None,
    negative_threshold: float = -0.25,
    news_lookback_days: int = 7,
    short_model: Scoreable | None = None,
    earnings_provider: object | None = None,
) -> dict:
    """Re-score a tracked position and attach live status + both exit reads.

    If a news provider + analyzer are supplied, material negative news adds a
    ``negative_news`` flag that tips the exit signal toward sell (build-plan §5).
    """
    scored = _score_now(
        position["ticker"],
        model,
        model_version,
        price_provider,
        fundamental_provider,
        storage,
        short_model,
        earnings_provider,
    )
    current_price = float(scored["price"])
    current_conviction = int(scored["conviction"])
    flags = list(scored["risk_flags"])

    entry_price = position["entry_price"]
    entry_conviction = position["entry_conviction"]
    price_change = _pct_change(current_price, entry_price)

    sentiment_score: float | None = None
    sentiment_label: str | None = None
    if news_provider is not None and analyzer is not None and position["status"] == "open":
        try:
            report = analyze_ticker(
                position["ticker"], news_provider, analyzer, news_lookback_days
            )
            sentiment_score = round(report.score, 3)
            sentiment_label = report.label
            if report.count and report.score < negative_threshold:
                flags.append("negative_news")
        except Exception:  # noqa: BLE001 — news is optional; never break the view
            pass

    if position["status"] == "sold":
        signal = "sold"
        expert = "You sold this position — tracking how it has moved since."
    else:
        signal = exit_signal(current_conviction, flags)
        expert = expert_view(
            entry_conviction, current_conviction, price_change, scored["drivers"], signal
        )
        if sentiment_label == "negative":
            expert += f" Recent news skews negative (sentiment {sentiment_score})."
        elif sentiment_label == "positive":
            expert += " Recent news skews positive."

    since_sold = _pct_change(current_price, position.get("sold_price"))

    quantity = float(position.get("quantity") or 1.0)
    cost_basis = quantity * entry_price
    market_value = quantity * current_price
    if position["status"] == "sold":
        sold_price = position.get("sold_price")
        pnl_dollar = quantity * (current_price - sold_price) if sold_price else 0.0
    else:
        pnl_dollar = market_value - cost_basis

    lots = position.get("lots") or []
    lot_info = _lot_summary(lots, current_price if position["status"] != "sold" else None)
    has_multiple = lot_info["lot_count"] > 1

    return {
        **position,
        "current_price": current_price,
        "current_conviction": current_conviction,
        "current_recommendation": scored["recommendation"],
        "current_flags": flags,
        "price_change_pct": price_change,
        "price_is_live": bool(scored.get("price_is_live", False)),
        "conviction_change": current_conviction - entry_conviction,
        "since_sold_pct": since_sold,
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
        "signal": signal,
        "expert_view": expert,
        "quantity": quantity,
        "avg_entry_price": lot_info["avg_entry_price"],
        "has_multiple_lots": has_multiple,
        "lots": lot_info["lots"],
        "cost_basis": round(cost_basis, 2),
        "market_value": round(market_value, 2),
        "pnl_dollar": round(pnl_dollar, 2),
    }


def list_position_views(
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage,
    news_provider: NewsProvider | None = None,
    analyzer: SentimentAnalyzer | None = None,
    negative_threshold: float = -0.25,
    news_lookback_days: int = 7,
    short_model: Scoreable | None = None,
    earnings_provider: object | None = None,
) -> list[dict]:
    """Return every tracked position with a fresh live status.

    Each full view costs a model score + EDGAR companyfacts fetch + news
    sentiment pass per position, which is far too heavy to redo on every
    home-screen refresh (and brutal while the nightly news collect hogs
    CPU). Views are therefore memoized briefly; the TTL trade-off means
    prices/convictions may lag by up to five minutes, which is fine for a
    status card. Sold positions and errors are never cached (they can be
    corrected client-side or transient).
    """
    cache_key = (
        model_version,
        negative_threshold,
        news_lookback_days,
        id(model),
        id(short_model) if short_model is not None else None,
    )
    now = time.monotonic()
    with _view_cache_lock:
        cached_at = _view_cache.get("ts")
        cached_views = _view_cache.get("views")
        if (
            isinstance(cached_at, float)
            and isinstance(cached_views, list)
            and now - cached_at < _VIEW_CACHE_TTL_SECONDS
            and _view_cache.get("key") == cache_key
        ):
            return cached_views

    views: list[dict] = []
    sellable = True
    for position in storage.list_positions():
        try:
            views.append(
                position_view(
                    position,
                    model,
                    model_version,
                    price_provider,
                    fundamental_provider,
                    storage,
                    news_provider=news_provider,
                    analyzer=analyzer,
                    negative_threshold=negative_threshold,
                    news_lookback_days=news_lookback_days,
                    short_model=short_model,
                    earnings_provider=earnings_provider,
                )
            )
        except Exception:  # noqa: BLE001 — a data hiccup shouldn't hide the whole list
            views.append({**position, "signal": "unavailable", "expert_view": ""})
            sellable = False

    with _view_cache_lock:
        if sellable:
            _view_cache["views"] = [dict(v) for v in views]
            _view_cache["ts"] = time.monotonic()
            _view_cache["key"] = cache_key
    return views


_VIEW_CACHE_TTL_SECONDS = 300.0
_view_cache: dict[str, object] = {}
_view_cache_lock = threading.Lock()


def _invalidate_view_cache() -> None:
    """Drop memoized views (e.g. after selling or adding a position)."""
    with _view_cache_lock:
        _view_cache.clear()


def sell_position(
    position_id: str,
    *,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage,
    short_model: Scoreable | None = None,
    earnings_provider: object | None = None,
) -> dict | None:
    """Mark a position sold at today's price and return its updated view."""
    position = storage.get_position(position_id)
    if position is None:
        return None
    _invalidate_view_cache()
    scored = _score_now(
        position["ticker"],
        model,
        model_version,
        price_provider,
        fundamental_provider,
        storage,
        short_model,
        earnings_provider,
    )
    storage.close_position(position_id, dt.datetime.now(), float(scored["price"]))
    updated = storage.get_position(position_id)
    assert updated is not None
    return position_view(
        updated,
        model,
        model_version,
        price_provider,
        fundamental_provider,
        storage,
        short_model=short_model,
        earnings_provider=earnings_provider,
    )
