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
) -> dict:
    return score_ticker(
        ticker,
        model=model,
        model_version=model_version,
        price_provider=price_provider,
        fundamental_provider=fundamental_provider,
        label_window_months=12,
        storage=None,
    )


def open_position(
    ticker: str,
    *,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage,
) -> dict:
    """Snapshot today's price + score for ``ticker`` and start tracking it."""
    scored = _score_now(
        ticker, model, model_version, price_provider, fundamental_provider
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
    )
    stored = storage.get_position(position_id)
    assert stored is not None
    return position_view(
        stored, model, model_version, price_provider, fundamental_provider
    )


def position_view(
    position: dict,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    news_provider: NewsProvider | None = None,
    analyzer: SentimentAnalyzer | None = None,
    negative_threshold: float = -0.25,
    news_lookback_days: int = 7,
) -> dict:
    """Re-score a tracked position and attach live status + both exit reads.

    If a news provider + analyzer are supplied, material negative news adds a
    ``negative_news`` flag that tips the exit signal toward sell (build-plan §5).
    """
    scored = _score_now(
        position["ticker"], model, model_version, price_provider, fundamental_provider
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

    return {
        **position,
        "current_price": current_price,
        "current_conviction": current_conviction,
        "current_recommendation": scored["recommendation"],
        "current_flags": flags,
        "price_change_pct": price_change,
        "conviction_change": current_conviction - entry_conviction,
        "since_sold_pct": since_sold,
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
        "signal": signal,
        "expert_view": expert,
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
) -> list[dict]:
    """Return every tracked position with a fresh live status."""
    views: list[dict] = []
    for position in storage.list_positions():
        try:
            views.append(
                position_view(
                    position,
                    model,
                    model_version,
                    price_provider,
                    fundamental_provider,
                    news_provider=news_provider,
                    analyzer=analyzer,
                    negative_threshold=negative_threshold,
                    news_lookback_days=news_lookback_days,
                )
            )
        except Exception:  # noqa: BLE001 — a data hiccup shouldn't hide the whole list
            views.append({**position, "signal": "unavailable", "expert_view": ""})
    return views


def sell_position(
    position_id: str,
    *,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    storage: Storage,
) -> dict | None:
    """Mark a position sold at today's price and return its updated view."""
    position = storage.get_position(position_id)
    if position is None:
        return None
    scored = _score_now(
        position["ticker"], model, model_version, price_provider, fundamental_provider
    )
    storage.close_position(position_id, dt.datetime.now(), float(scored["price"]))
    updated = storage.get_position(position_id)
    assert updated is not None
    return position_view(
        updated, model, model_version, price_provider, fundamental_provider
    )
