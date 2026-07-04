"""FastAPI service exposing on-demand, explained conviction scores.

Endpoints:
- ``GET /health``        -> liveness + whether a trained model is loaded.
- ``GET /score/{ticker}`` -> conviction score + SHAP "why" + risk flags (build-plan §7).

State (model, providers, storage) is built once and injected via a FastAPI
dependency so tests can override it with fakes — no network required.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request

from stock_monitor import __version__
from stock_monitor.config import get_settings
from stock_monitor.earnings import EarningsProvider, get_earnings_provider
from stock_monitor.models.registry import compute_model_version, load_model
from stock_monitor.models.scorer import Scoreable
from stock_monitor.positions import (
    list_position_views,
    open_position,
    sell_position,
)
from stock_monitor.providers.edgar_provider import EdgarProvider
from stock_monitor.providers.yfinance_provider import YFinanceProvider
from stock_monitor.sentiment import (
    NewsProvider,
    SentimentAnalyzer,
    analyze_ticker,
    get_news_provider,
    get_sentiment_analyzer,
)
from stock_monitor.service import (
    DataQuarantined,
    InsufficientHistory,
    TickerDataUnavailable,
    score_ticker,
    strong_recommendations,
)
from stock_monitor.storage.db import Storage


@dataclass
class AppState:
    """Injected runtime state for the API."""

    model: Scoreable | None
    model_version: str | None
    price_provider: object
    fundamental_provider: object
    db_path: str | None
    label_window_months: int
    model_short: Scoreable | None = None
    news_provider: NewsProvider | None = None
    analyzer: SentimentAnalyzer | None = None
    news_lookback_days: int = 7
    sentiment_negative_threshold: float = -0.25
    earnings_provider: EarningsProvider | None = None


_state: AppState | None = None


def build_state() -> AppState:
    """Construct the default production state (loads the persisted model)."""
    settings = get_settings()
    model = load_model(settings.model_path)
    version = compute_model_version(model) if model is not None else None
    return AppState(
        model=model,
        model_version=version,
        price_provider=YFinanceProvider(),
        fundamental_provider=EdgarProvider(),
        db_path=settings.db_path,
        label_window_months=settings.label_window_months,
        model_short=load_model(settings.model_path_short),
        news_provider=get_news_provider(settings),
        analyzer=get_sentiment_analyzer(settings),
        news_lookback_days=settings.news_lookback_days,
        sentiment_negative_threshold=settings.sentiment_negative_threshold,
        earnings_provider=get_earnings_provider(settings),
    )


def get_state() -> AppState:
    """FastAPI dependency: build state once, reuse it (overridable in tests)."""
    global _state
    if _state is None:
        _state = build_state()
    return _state


def require_api_key(request: Request) -> None:
    """Reject requests lacking the shared secret when one is configured.

    Auth is disabled (open) when ``API_SHARED_SECRET`` is unset — convenient for
    local dev. ``/health`` stays open so uptime checks work without the key.
    """
    secret = get_settings().api_shared_secret
    if not secret or request.url.path == "/health":
        return
    provided = request.headers.get("x-api-key", "")
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Optionally run the scheduler in-process (one DuckDB owner) for deployments."""
    settings = get_settings()
    scheduler = None
    if settings.run_scheduler:
        from stock_monitor.notify import get_notifier
        from stock_monitor.scheduler import build_background_scheduler

        scheduler = build_background_scheduler(settings, get_notifier(settings))
        scheduler.start()
        logging.getLogger("stock_monitor.api").info("in-process scheduler started")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(
    title="Stock-Monitor API",
    version=__version__,
    description="Explainable, human-in-the-loop stock conviction scoring. No auto-trading.",
    lifespan=lifespan,
    dependencies=[Depends(require_api_key)],
)

StateDep = Annotated[AppState, Depends(get_state)]


@app.get("/health")
def health(state: StateDep) -> dict[str, object]:
    return {
        "status": "ok",
        "model_loaded": state.model is not None,
        "model_version": state.model_version,
    }


@app.get("/score/{ticker}")
def score(ticker: str, state: StateDep) -> dict[str, object]:
    if state.model is None:
        raise HTTPException(
            status_code=503,
            detail="no trained model available; run `stock-monitor-train` first",
        )
    try:
        # Short-lived DB connection per request (DuckDB is single-writer across
        # processes, so the scan CLI can write while the API is running).
        if state.db_path:
            with Storage(state.db_path) as store:
                return score_ticker(
                    ticker,
                    model=state.model,
                    model_version=state.model_version or "unknown",
                    price_provider=state.price_provider,  # type: ignore[arg-type]
                    fundamental_provider=state.fundamental_provider,  # type: ignore[arg-type]
                    label_window_months=state.label_window_months,
                    storage=store,
                    short_model=state.model_short,
                    earnings_provider=state.earnings_provider,
                )
        return score_ticker(
            ticker,
            model=state.model,
            model_version=state.model_version or "unknown",
            price_provider=state.price_provider,  # type: ignore[arg-type]
            fundamental_provider=state.fundamental_provider,  # type: ignore[arg-type]
            label_window_months=state.label_window_months,
            storage=None,
            short_model=state.model_short,
            earnings_provider=state.earnings_provider,
        )
    except TickerDataUnavailable as exc:
        raise HTTPException(status_code=404, detail=f"no price data for {ticker.upper()}") from exc
    except InsufficientHistory as exc:
        raise HTTPException(
            status_code=422, detail=f"insufficient price history for {ticker.upper()}"
        ) from exc
    except DataQuarantined as exc:
        raise HTTPException(status_code=422, detail=f"data quarantined: {exc}") from exc


@app.get("/opportunities")
def opportunities(state: StateDep, limit: int = 20) -> dict[str, object]:
    """Return the latest ranked "top-N to buy now" list from the most recent scan."""
    if not state.db_path:
        return {"scanned_at": None, "opportunities": [], "note": "storage unavailable"}
    with Storage(state.db_path) as store:
        ranked = store.read_latest_opportunities(limit=limit)
    scanned_at = ranked[0]["scan_ts"] if ranked else None
    note = None if ranked else "no scan yet — run `stock-monitor-scan`"
    return {"scanned_at": scanned_at, "opportunities": ranked, "note": note}


@app.get("/recommendations")
def recommendations(state: StateDep) -> dict[str, object]:
    """Return only high-confidence buys (sparse by design) with a plain-language why."""
    if not state.db_path:
        return {"scanned_at": None, "recommendations": [], "note": "storage unavailable"}
    with Storage(state.db_path) as store:
        ranked = store.read_latest_opportunities(limit=1000)
    strong = strong_recommendations(ranked)
    scanned_at = ranked[0]["scan_ts"] if ranked else None
    note = (
        None
        if strong
        else "No high-confidence buys right now — the tool only flags names it's sure about."
    )
    return {"scanned_at": scanned_at, "recommendations": strong, "note": note}


def _require_ready(state: AppState) -> None:
    if state.model is None:
        raise HTTPException(
            status_code=503, detail="no trained model; run `stock-monitor-train` first"
        )
    if not state.db_path:
        raise HTTPException(status_code=503, detail="storage unavailable")


@app.get("/positions")
def positions(state: StateDep) -> dict[str, object]:
    """List tracked positions with a fresh live status + exit reads (news-aware)."""
    if state.model is None or not state.db_path:
        return {"positions": []}
    with Storage(state.db_path) as store:
        views = list_position_views(
            state.model,
            state.model_version or "unknown",
            state.price_provider,  # type: ignore[arg-type]
            state.fundamental_provider,  # type: ignore[arg-type]
            store,
            news_provider=state.news_provider,
            analyzer=state.analyzer,
            negative_threshold=state.sentiment_negative_threshold,
            news_lookback_days=state.news_lookback_days,
        )
    return {"positions": views}


@app.get("/news/{ticker}")
def news(ticker: str, state: StateDep) -> dict[str, object]:
    """Recent headlines for a ticker with per-headline + aggregate sentiment."""
    if state.news_provider is None or state.analyzer is None:
        return {"ticker": ticker.upper(), "score": 0.0, "label": "neutral", "items": []}
    report = analyze_ticker(
        ticker,
        state.news_provider,
        state.analyzer,
        lookback_days=state.news_lookback_days,
    )
    return {
        "ticker": report.ticker,
        "score": round(report.score, 3),
        "label": report.label,
        "count": report.count,
        "backend": report.backend,
        "items": [
            {
                "headline": i.headline,
                "url": i.url,
                "source": i.source,
                "published": i.published.isoformat() if i.published else None,
                "sentiment": round(i.sentiment, 3) if i.sentiment is not None else None,
            }
            for i in report.items
        ],
    }


@app.post("/positions/{ticker}")
def add_position(ticker: str, state: StateDep) -> dict[str, object]:
    """Start tracking a ticker, snapshotting today's price + score as the entry."""
    _require_ready(state)
    try:
        with Storage(state.db_path) as store:  # type: ignore[arg-type]
            return open_position(
                ticker,
                model=state.model,  # type: ignore[arg-type]
                model_version=state.model_version or "unknown",
                price_provider=state.price_provider,  # type: ignore[arg-type]
                fundamental_provider=state.fundamental_provider,  # type: ignore[arg-type]
                storage=store,
            )
    except TickerDataUnavailable as exc:
        raise HTTPException(status_code=404, detail=f"no price data for {ticker.upper()}") from exc
    except (InsufficientHistory, DataQuarantined) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/positions/{position_id}/sell")
def sell(position_id: str, state: StateDep) -> dict[str, object]:
    """Mark a tracked position sold at today's price."""
    _require_ready(state)
    with Storage(state.db_path) as store:  # type: ignore[arg-type]
        view = sell_position(
            position_id,
            model=state.model,  # type: ignore[arg-type]
            model_version=state.model_version or "unknown",
            price_provider=state.price_provider,  # type: ignore[arg-type]
            fundamental_provider=state.fundamental_provider,  # type: ignore[arg-type]
            storage=store,
        )
    if view is None:
        raise HTTPException(status_code=404, detail="position not found")
    return view
