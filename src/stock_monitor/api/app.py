"""FastAPI service exposing on-demand, explained conviction scores.

Endpoints:
- ``GET /health``        -> liveness + whether a trained model is loaded.
- ``GET /score/{ticker}`` -> conviction score + SHAP "why" + risk flags (build-plan §7).

State (model, providers, storage) is built once and injected via a FastAPI
dependency so tests can override it with fakes — no network required.
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request

from stock_monitor import __version__
from stock_monitor.config import get_settings
from stock_monitor.earnings import EarningsProvider, get_earnings_provider
from stock_monitor.features.builder import build_feature_row
from stock_monitor.models.registry import compute_model_version, load_model
from stock_monitor.models.scorer import Scoreable
from stock_monitor.positions import (
    list_position_views,
    open_position,
    sell_position,
)
from stock_monitor.providers import get_price_provider
from stock_monitor.providers.edgar_provider import EdgarProvider
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

# Tracks the in-process, on-demand scan triggered from the UI's "Refresh" button.
# Only one scan runs at a time (the lock), and DuckDB stays a single owner because
# the scan runs *inside* the API process — no separate `stock-monitor-scan` needed.
_scan_lock = threading.Lock()
_scan_status: dict[str, object] = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_count": None,
    "last_error": None,
}

# Cache EOD price bars per (ticker, days) so the candlestick chart never hammers
# the price provider's free-tier rate limit. Bars only change once per day (after
# market close), so a modest in-process TTL serves the UI from memory and makes at
# most a handful of upstream calls per ticker per hour.
_PRICE_CACHE: dict[str, tuple[float, list[dict[str, object]]]] = {}
_PRICE_CACHE_LOCK = threading.Lock()
_PRICE_CACHE_TTL_SECONDS = 3600.0


def build_state() -> AppState:
    """Construct the default production state (loads the persisted model)."""
    settings = get_settings()
    model = load_model(settings.model_path)
    version = compute_model_version(model) if model is not None else None
    return AppState(
        model=model,
        model_version=version,
        price_provider=get_price_provider(settings),
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


@app.get("/prices/{ticker}")
def prices(ticker: str, state: StateDep, days: int = 180) -> dict[str, object]:
    """Adjusted daily OHLCV bars for a ticker (candlestick chart data).

    Cached in-process per (ticker, days) so the chart never hammers the price
    provider's free-tier rate limit — EOD bars change at most once per day, so we
    serve repeat views from memory and make at most a handful of upstream calls.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker required")
    days = max(20, min(int(days), 365 * 5))
    key = f"{ticker}:{days}"
    now = time.monotonic()
    with _PRICE_CACHE_LOCK:
        hit = _PRICE_CACHE.get(key)
        if hit is not None and now - hit[0] < _PRICE_CACHE_TTL_SECONDS:
            return {"ticker": ticker, "days": days, "cached": True, "bars": hit[1]}

    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    try:
        frame = state.price_provider.get_prices(ticker, start, end)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — upstream/network failure surfaces as 502
        raise HTTPException(
            status_code=502, detail=f"price data unavailable for {ticker}"
        ) from exc

    bars: list[dict[str, object]] = []
    if frame is not None and not frame.empty:
        for idx, row in frame.iterrows():
            try:
                vol = float(row["volume"])
                bars.append(
                    {
                        "time": idx.date().isoformat(),
                        "open": round(float(row["open"]), 4),
                        "high": round(float(row["high"]), 4),
                        "low": round(float(row["low"]), 4),
                        "close": round(float(row["close"]), 4),
                        "volume": int(vol) if vol == vol else 0,
                    }
                )
            except (TypeError, ValueError, KeyError):
                continue
    if not bars:
        raise HTTPException(status_code=404, detail=f"no price data for {ticker}")

    with _PRICE_CACHE_LOCK:
        _PRICE_CACHE[key] = (now, bars)
    return {"ticker": ticker, "days": days, "cached": False, "bars": bars}


def _run_scan_bg(state: AppState) -> None:
    """Run a universe scan in the API process, then release the lock. Never raises."""
    from stock_monitor.scan import scan_job

    settings = get_settings()
    log = logging.getLogger("stock_monitor.api")
    try:
        ranked = scan_job(
            settings,
            model=state.model,
            price_provider=state.price_provider,  # type: ignore[arg-type]
            fundamental_provider=state.fundamental_provider,  # type: ignore[arg-type]
        )
        _scan_status["last_count"] = len(ranked)
        _scan_status["last_error"] = None
        log.info("on-demand scan finished: %d scored", len(ranked))
    except Exception as exc:  # noqa: BLE001 — surface the error via status, don't crash
        _scan_status["last_error"] = str(exc)
        log.exception("on-demand scan failed")
    finally:
        _scan_status["running"] = False
        _scan_status["last_finished"] = dt.datetime.now().isoformat()
        _scan_lock.release()


@app.post("/scan")
def trigger_scan(state: StateDep, background: BackgroundTasks) -> dict[str, object]:
    """Kick off a fresh universe scan in the background (the UI "Refresh" button).

    Runs in-process so DuckDB stays single-owner. If a scan is already running,
    this is a no-op that reports the in-flight status instead of starting a second.
    """
    _require_ready(state)
    if not _scan_lock.acquire(blocking=False):
        return {"status": "already_running", **_scan_status}
    _scan_status["running"] = True
    _scan_status["last_started"] = dt.datetime.now().isoformat()
    background.add_task(_run_scan_bg, state)
    return {"status": "started", **_scan_status}


@app.get("/scan/status")
def scan_status() -> dict[str, object]:
    """Poll target for the UI: is a scan running, and how did the last one go?"""
    return dict(_scan_status)


@app.get("/paper/summary")
def paper_summary_endpoint(state: StateDep) -> dict[str, object]:
    """Paper-mode track record: hit-rate + avg excess return vs SPY on matured picks.

    This is the honest "does it work?" scoreboard — simulated buys of the daily
    buy-zone names, scored against the benchmark once their horizon matures.
    """
    from stock_monitor.paper import paper_summary

    if not state.db_path:
        return {"summary": None, "note": "storage unavailable"}
    with Storage(state.db_path) as store:
        summary = paper_summary(store)
    note = None if summary["closed"] else "No matured paper picks yet — check back later."
    return {"summary": summary, "note": note}


@app.get("/scorecard")
def scorecard_endpoint(state: StateDep) -> dict[str, object]:
    """Edge scorecard: a plain 🟢/🟡/🔴 verdict on whether the model has proven itself.

    Combines the historical backtest (beat SPY?) with the live paper track record
    (are the simulated buys beating SPY as they mature?) into one honest answer to
    "is it safe to trust this with real money yet?".
    """
    from stock_monitor.scorecard import build_scorecard

    if not state.db_path:
        return {"verdict": "building", "note": "storage unavailable"}
    with Storage(state.db_path) as store:
        return build_scorecard(store)


def _news_trend_from_history(sentiment: object) -> dict[str, object] | None:
    """Summarize backfilled news sentiment into a plain trajectory (recent vs prior).

    Returns direction (improving/deteriorating/flat), the recent/prior 90-day means, the
    latest reading, and coverage. ``None`` when there is no stored sentiment history.
    """
    import pandas as pd

    if sentiment is None or getattr(sentiment, "empty", True):
        return None
    d = sentiment.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date")
    latest_date = d["date"].max()
    recent = d[d["date"] >= latest_date - pd.Timedelta(days=90)]
    prior = d[
        (d["date"] < latest_date - pd.Timedelta(days=90))
        & (d["date"] >= latest_date - pd.Timedelta(days=180))
    ]
    if recent.empty:
        return None
    recent_mean = float(recent["sentiment"].mean())
    prior_mean = float(prior["sentiment"].mean()) if not prior.empty else None
    if prior_mean is None:
        direction, delta = "flat", 0.0
    else:
        delta = recent_mean - prior_mean
        direction = (
            "improving" if delta > 0.05 else "deteriorating" if delta < -0.05 else "flat"
        )
    return {
        "direction": direction,
        "recent_90d_mean": round(recent_mean, 3),
        "prior_90d_mean": round(prior_mean, 3) if prior_mean is not None else None,
        "latest": round(float(d.iloc[-1]["sentiment"]), 3),
        "delta": round(delta, 3),
        "days_covered": int(d["date"].dt.date.nunique()),
        "backend": str(d.iloc[-1].get("backend", "")),
    }


def _analyst_history_evidence(ticker: str, state: AppState) -> dict[str, object]:
    """Best-effort learn-from-history evidence for the AI analyst.

    Builds today's PIT feature row, finds similar past setups (empirical base rate), and
    summarizes the backfilled news-sentiment trend. Never raises — enrichment is optional
    and the second opinion still stands without it.
    """
    out: dict[str, object] = {}
    if not state.db_path:
        return out
    try:
        end = dt.date.today()
        start = end - dt.timedelta(days=365 * 8)
        prices = state.price_provider.get_prices(ticker, start, end)  # type: ignore[attr-defined]
        if prices.empty:
            return out
        facts = state.fundamental_provider.get_fundamentals(ticker)  # type: ignore[attr-defined]
        as_of = prices.index[-1].date()
        row = build_feature_row(ticker, prices, facts, as_of)
        if row is None:
            return out
        from stock_monitor.similar import find_similar_setups

        with Storage(state.db_path) as store:
            history = store.read_features()
            sentiment = store.read_news_sentiment(ticker.upper())
        out["similar"] = find_similar_setups(row, history, k=5)
        trend = _news_trend_from_history(sentiment)
        if trend is not None:
            out["news_trend"] = trend
    except Exception:  # noqa: BLE001 — enrichment is best-effort, never fatal
        logging.getLogger("stock_monitor.api").debug(
            "analyst enrichment failed for %s", ticker, exc_info=True
        )
    return out


@app.get("/analyst/{ticker}")
def analyst(ticker: str, state: StateDep) -> dict[str, object]:
    """Optional LLM second opinion on a ticker (opt-in; disabled by default, has a cost).

    Scores the ticker with the primary model, attaches recent news sentiment, the
    learn-from-history analog base rate, and the news-sentiment trend, then asks the
    configured LLM for an independent BUY/HOLD/SELL read. The model score is always
    the signal of record — this is a second opinion for a human to weigh.
    """
    from stock_monitor.analyst import second_opinion

    settings = get_settings()
    upper = ticker.upper()
    if not settings.llm_analyst_enabled or not settings.openai_api_key:
        return {
            "ticker": upper,
            "opinion": None,
            "note": "AI analyst disabled — set LLM_ANALYST_ENABLED=1 and OPENAI_API_KEY.",
        }
    if state.model is None:
        raise HTTPException(status_code=503, detail="no trained model available")

    try:
        payload = score_ticker(
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
        raise HTTPException(status_code=404, detail=f"no price data for {upper}") from exc
    except (InsufficientHistory, DataQuarantined) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if state.news_provider is not None and state.analyzer is not None:
        report = analyze_ticker(
            ticker, state.news_provider, state.analyzer,
            lookback_days=state.news_lookback_days,
        )
        payload["news_sentiment"] = round(report.score, 3)
        payload["news_label"] = report.label

    # Learn-from-history evidence: analog base rate + news-sentiment trend (best-effort).
    payload.update(_analyst_history_evidence(ticker, state))

    opinion = second_opinion(payload, settings)
    return {
        "ticker": upper,
        "opinion": opinion,
        "note": None if opinion else "AI analyst unavailable (LLM call failed).",
    }


@app.get("/similar/{ticker}")
def similar(ticker: str, state: StateDep, limit: int = 5) -> dict[str, object]:
    """Find past setups like today's and how they played out (learn-from-history signal).

    Builds today's PIT feature row for the ticker, then finds the most similar labelled
    setups across all of local history and reports how many beat the benchmark — an
    empirical, no-lookahead base rate that adds confidence by analogy to the model score.
    """
    from stock_monitor.similar import find_similar_setups

    upper = ticker.upper()
    if not state.db_path:
        return {"ticker": upper, "similar": None, "note": "storage unavailable"}

    end = dt.date.today()
    start = end - dt.timedelta(days=365 * 8)
    prices = state.price_provider.get_prices(ticker, start, end)  # type: ignore[attr-defined]
    if prices.empty:
        raise HTTPException(status_code=404, detail=f"no price data for {upper}")
    facts = state.fundamental_provider.get_fundamentals(ticker)  # type: ignore[attr-defined]
    as_of = prices.index[-1].date()
    row = build_feature_row(ticker, prices, facts, as_of)
    if row is None:
        raise HTTPException(status_code=422, detail=f"insufficient history for {upper}")

    with Storage(state.db_path) as store:
        history = store.read_features()
    result = find_similar_setups(row, history, k=limit)
    note = (
        None
        if result["analogs"]
        else "Not enough labelled history yet — train on more names/dates to enable this."
    )
    return {"ticker": upper, "as_of": as_of.isoformat(), "similar": result, "note": note}


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
