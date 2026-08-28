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
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from stock_monitor import __version__
from stock_monitor.config import Settings, get_settings
from stock_monitor.earnings import EarningsProvider, get_earnings_provider
from stock_monitor.features.builder import build_feature_row
from stock_monitor.metrics import SCORE_LATENCY, SCORES_SERVED
from stock_monitor.models.registry import compute_model_version, load_model
from stock_monitor.models.scorer import Scoreable
from stock_monitor.positions import (
    list_position_views,
    open_position,
    sell_position,
)
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
from stock_monitor.symbols import SymbolDirectory

if TYPE_CHECKING:
    import pandas as pd


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
    symbol_directory: object | None = None


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
    "progress": None,
}

# Tracks the in-process, on-demand news collect+archive triggered from the UI's
# "Update news" button (separate from Refresh so the user controls it explicitly).
_news_lock = threading.Lock()
_news_status: dict[str, object] = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_archived": None,
    "last_error": None,
    "progress": None,
}

# Tracks the one-time, resumable Alpha Vantage gap backfill (2024-01 -> ~Finnhub's
# 1-year reach). Runs in-process (single DuckDB owner), capped per run at the free
# 25/day quota, resuming across nights via the news_backfill_state table.
_avbf_lock = threading.Lock()
_avbf_status: dict[str, object] = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_calls": None,
    "last_written": None,
    "last_archived": None,
    "tickers_done": None,
    "tickers_total": None,
    "stopped": None,
    "last_error": None,
    "progress": None,
}

# Cache EOD price bars per (ticker, days) so the candlestick chart never hammers
# the price provider's free-tier rate limit. Bars only change once per day (after
# market close), so a modest in-process TTL serves the UI from memory and makes at
# most a handful of upstream calls per ticker per hour.
_PRICE_CACHE: dict[str, tuple[float, list[dict[str, object]]]] = {}
_PRICE_CACHE_LOCK = threading.Lock()
_PRICE_CACHE_TTL_SECONDS = 3600.0


def _score_price_provider(settings: Settings) -> object:
    """Price source for on-demand scoring and charts.

    Served from the local price cache (``data/prices.duckdb``) with a yfinance
    upstream — the same source the daily scan uses. This keeps the on-demand card
    and the ranked list on identical prices, and (critically) never touches Tiingo's
    ~50-req/hr free-tier cap, which was 429-ing every single-ticker click. Cached
    universe names read straight from disk; a searched ticker not yet in the cache is
    fetched once from yfinance and stored (``fetch_missing=True``).
    """
    from stock_monitor.providers.price_cache import CachedPriceProvider, PriceCache
    from stock_monitor.providers.yfinance_provider import YFinanceProvider

    upstream = YFinanceProvider()
    if settings.use_price_cache:
        return CachedPriceProvider(
            upstream, PriceCache(settings.price_cache_path), fetch_missing=True
        )
    return upstream


def build_state() -> AppState:
    """Construct the default production state (loads the persisted model)."""
    settings = get_settings()
    model = load_model(settings.model_path)
    version = compute_model_version(model) if model is not None else None
    return AppState(
        model=model,
        model_version=version,
        price_provider=_score_price_provider(settings),
        fundamental_provider=EdgarProvider(),
        db_path=settings.db_path,
        label_window_months=settings.label_window_months,
        model_short=load_model(settings.model_path_short),
        news_provider=get_news_provider(settings),
        analyzer=get_sentiment_analyzer(settings),
        news_lookback_days=settings.news_lookback_days,
        sentiment_negative_threshold=settings.sentiment_negative_threshold,
        earnings_provider=get_earnings_provider(settings),
        symbol_directory=SymbolDirectory(),
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
    if settings.metrics_enabled:
        from stock_monitor.metrics import start_metrics_server

        start_metrics_server(settings.metrics_port, settings.db_path)
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
    start = time.perf_counter()
    try:
        # Short-lived DB connection per request (DuckDB is single-writer across
        # processes, so the scan CLI can write while the API is running).
        if state.db_path:
            with Storage(state.db_path) as store:
                result = score_ticker(
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
        else:
            result = score_ticker(
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
        SCORES_SERVED.labels(outcome="ok").inc()
        # Attach the full company name (for the scoring card header); best-effort.
        if state.symbol_directory is not None:
            result["name"] = state.symbol_directory.name_for(ticker)  # type: ignore[attr-defined]
        return result
    except TickerDataUnavailable as exc:
        SCORES_SERVED.labels(outcome="no_data").inc()
        raise HTTPException(status_code=404, detail=f"no price data for {ticker.upper()}") from exc
    except InsufficientHistory as exc:
        SCORES_SERVED.labels(outcome="insufficient_history").inc()
        raise HTTPException(
            status_code=422, detail=f"insufficient price history for {ticker.upper()}"
        ) from exc
    except DataQuarantined as exc:
        SCORES_SERVED.labels(outcome="quarantined").inc()
        raise HTTPException(status_code=422, detail=f"data quarantined: {exc}") from exc
    finally:
        SCORE_LATENCY.observe(time.perf_counter() - start)


@app.get("/search")
def search_symbols(state: StateDep, q: str = "", limit: int = 15) -> dict[str, object]:
    """Search the SEC ticker registry by company name or symbol (for the search box).

    Lets the user find a stock by name ("apple") when they don't know the ticker.
    Returns lightweight ``{ticker, name}`` matches, best first.
    """
    if state.symbol_directory is None or not q.strip():
        return {"query": q, "results": []}
    matches = state.symbol_directory.search(q, limit=max(1, min(limit, 50)))  # type: ignore[attr-defined]
    return {
        "query": q,
        "results": [{"ticker": m.ticker, "name": m.name} for m in matches],
    }


def _merge_on_demand_scores(
    ranked: list[dict], recent: list[dict]
) -> tuple[list[dict], int]:
    """Merge recent on-demand scores into a scan ranking, re-rank, return (rows, added).

    Tickers the user looked up manually (outside the nightly universe) get discovered
    into the ranked page when their conviction is fresh enough to matter. Scan rows
    keep their identity; on-demand entries are marked ``source="on_demand"`` so the
    UI can be honest about where each row came from. Duplicates (a scan ticker also
    scored on demand) always keep the *scan* row.
    """
    scan_tickers = {r["ticker"] for r in ranked}
    extras = [
        {
            "rank": 0,
            "ticker": s["ticker"],
            "conviction": s["conviction"],
            "capped_conviction": s["conviction"],
            "recommendation": s["recommendation"],
            "as_of": s["as_of"],
            "risk_flags": s["risk_flags"],
            "model_version": s["model_version"],
            "scan_ts": None,
            "source": "on_demand",
        }
        for s in recent
        if s["ticker"] not in scan_tickers
    ]
    if not extras:
        for i, r in enumerate(ranked, start=1):
            r["rank"] = i
            r.setdefault("source", "scan")
        return ranked, 0

    merged = sorted(
        [*[{**r, "source": "scan"} for r in ranked], *extras],
        key=lambda r: r["capped_conviction"],
        reverse=True,
    )
    for i, r in enumerate(merged, start=1):
        r["rank"] = i
    return merged, len(extras)


@app.get("/opportunities")
def opportunities(state: StateDep, limit: int = 20) -> dict[str, object]:
    """Return the latest ranked "top-N to buy now" list from the most recent scan.

    Freshly-scored on-demand tickers (searched manually, outside the nightly
    universe) are merged in so discovery isn't capped at the curated list.
    """
    if not state.db_path:
        return {"scanned_at": None, "opportunities": [], "note": "storage unavailable"}
    with Storage(state.db_path) as store:
        ranked = store.read_latest_opportunities(limit=limit)
        try:
            recent = store.read_recent_scores(within_days=3)
        except Exception:  # noqa: BLE001 — merge is an enhancement; never break list
            recent = []
    ranked, on_demand_count = _merge_on_demand_scores(ranked, recent)
    scanned_at = next((r.get("scan_ts") for r in ranked if r.get("scan_ts")), None)
    note = None if ranked else "no scan yet — run `stock-monitor-scan`"
    return {
        "scanned_at": scanned_at,
        "opportunities": ranked,
        "on_demand_count": on_demand_count,
        "note": note,
    }


@app.get("/recommendations")
def recommendations(state: StateDep) -> dict[str, object]:
    """Return only high-confidence buys (sparse by design) with a plain-language why."""
    if not state.db_path:
        return {"scanned_at": None, "recommendations": [], "note": "storage unavailable"}
    with Storage(state.db_path) as store:
        ranked = store.read_latest_opportunities(limit=1000)
        try:
            recent = store.read_recent_scores(within_days=3)
        except Exception:  # noqa: BLE001 — merge is an enhancement; never break list
            recent = []
    ranked, _ = _merge_on_demand_scores(ranked, recent)
    strong = strong_recommendations(ranked)
    scanned_at = next((r.get("scan_ts") for r in ranked if r.get("scan_ts")), None)
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
            short_model=state.model_short,
            earnings_provider=state.earnings_provider,
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

    def _progress(done: int, total: int) -> None:
        _scan_status["progress"] = {"done": done, "total": total}

    try:
        ranked = scan_job(
            settings,
            model=state.model,
            price_provider=state.price_provider,  # type: ignore[arg-type]
            fundamental_provider=state.fundamental_provider,  # type: ignore[arg-type]
            progress_cb=_progress,
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
    _scan_status["progress"] = {"done": 0, "total": 0}
    background.add_task(_run_scan_bg, state)
    return {"status": "started", **_scan_status}


@app.get("/scan/status")
def scan_status() -> dict[str, object]:
    """Poll target for the UI: is a scan running, and how did the last one go?"""
    return dict(_scan_status)


def _run_news_collect_bg(days: int) -> None:
    """Run an on-demand news collect+archive in the API process. Never raises."""
    from stock_monitor.scheduler import collect_daily_news

    settings = get_settings()
    log = logging.getLogger("stock_monitor.api")

    def _progress(done: int, total: int) -> None:
        _news_status["progress"] = {"done": done, "total": total}

    try:
        archived = collect_daily_news(
            settings, lookback_days=days, progress_cb=_progress
        )
        _news_status["last_archived"] = archived
        _news_status["last_error"] = None
        log.info("on-demand news collection archived %d rows", archived)
    except Exception as exc:  # noqa: BLE001 — surface via status, don't crash
        _news_status["last_error"] = str(exc)
        log.exception("on-demand news collection failed")
    finally:
        _news_status["running"] = False
        _news_status["last_finished"] = dt.datetime.now().isoformat()
        _news_lock.release()


@app.post("/news/collect")
def trigger_news_collect(
    state: StateDep, background: BackgroundTasks, days: int = 7
) -> dict[str, object]:
    """Kick off an on-demand news collect+archive (the UI "Update news" button).

    Runs in-process so DuckDB stays single-owner. Idempotent — re-runs skip days
    already stored. Independent of the model (needs only storage + a news provider).
    """
    if not state.db_path:
        raise HTTPException(status_code=503, detail="storage unavailable")
    # Button sends days=7; larger values (up to ~1yr, the free news window) let this
    # same endpoint drive an on-demand historical backfill without a second DB owner.
    days = max(1, min(int(days), 365))
    if not _news_lock.acquire(blocking=False):
        return {"status": "already_running", **_news_status}
    _news_status["running"] = True
    _news_status["last_started"] = dt.datetime.now().isoformat()
    _news_status["progress"] = {"done": 0, "total": 0}
    background.add_task(_run_news_collect_bg, days)
    return {"status": "started", **_news_status}


@app.get("/news/collect/status")
def news_collect_status() -> dict[str, object]:
    """Poll target for the UI: is a news collection running, how did it go, and how
    fresh is our news?

    ``days_since`` (days since the most recent stored news day) lets the UI show, on
    entry, how long it's been since news last updated — so the user knows when to hit
    "Update news". The nightly job keeps this at 0-1 when the VM is awake.
    """
    status = dict(_news_status)
    latest: dt.date | None = None
    try:
        with Storage(get_settings().db_path) as storage:
            latest = storage.latest_news_date()
    except Exception:  # noqa: BLE001 — freshness is best-effort, never break the poll
        latest = None
    if latest is not None:
        status["last_news_date"] = latest.isoformat()
        status["days_since"] = (dt.date.today() - latest).days
    else:
        status["last_news_date"] = None
        status["days_since"] = None
    return status


def _run_av_backfill_bg() -> None:
    """Run one resumable Alpha Vantage gap-backfill pass in the API process.

    Single DuckDB owner + free 25/day quota → this runs in-process, capped, and
    resumes across nights via the news_backfill_state table. Never raises.
    """
    from datetime import date, timedelta

    from stock_monitor.backfill import backfill_gap_news
    from stock_monitor.providers.alphavantage_provider import AlphaVantageNewsProvider
    from stock_monitor.universe import get_scan_universe

    settings = get_settings()
    log = logging.getLogger("stock_monitor.api")

    def _progress(done: int, total: int) -> None:
        _avbf_status["progress"] = {"done": done, "total": total}

    try:
        provider = AlphaVantageNewsProvider(settings.alphavantage_api_key)
        start = date.fromisoformat(settings.news_gap_start)
        # Finnhub already covers the trailing ~year; AV fills only the older gap.
        end = date.today() - timedelta(days=365)
        tickers = get_scan_universe(settings)
        with Storage(settings.db_path) as storage:
            summary = backfill_gap_news(
                settings,
                provider,
                storage,
                tickers,
                start,
                end,
                max_calls=settings.news_gap_backfill_max_calls,
                progress_cb=_progress,
            )
        _avbf_status.update(
            {
                "last_calls": summary["calls"],
                "last_written": summary["rows_written"],
                "last_archived": summary["archived"],
                "tickers_done": summary["tickers_done"],
                "tickers_total": summary["tickers_total"],
                "stopped": summary["stopped"],
                "last_error": None,
            }
        )
        log.info("AV gap backfill pass: %s", summary)
    except Exception as exc:  # noqa: BLE001 — surface via status, don't crash
        _avbf_status["last_error"] = str(exc)
        log.exception("AV gap backfill failed")
    finally:
        _avbf_status["running"] = False
        _avbf_status["last_finished"] = dt.datetime.now().isoformat()
        _avbf_lock.release()


@app.post("/news/backfill-av")
def trigger_av_backfill(
    state: StateDep, background: BackgroundTasks
) -> dict[str, object]:
    """Kick off one Alpha Vantage gap-backfill pass (in-process, DuckDB-safe).

    Capped at the free 25/day quota and resumable — call again on later nights until
    ``stopped == "complete"``. 503s if storage or the AV key is missing.
    """
    if not state.db_path:
        raise HTTPException(status_code=503, detail="storage unavailable")
    if not get_settings().alphavantage_api_key:
        raise HTTPException(status_code=503, detail="alphavantage key not configured")
    if not _avbf_lock.acquire(blocking=False):
        return {"status": "already_running", **_avbf_status}
    _avbf_status["running"] = True
    _avbf_status["last_started"] = dt.datetime.now().isoformat()
    _avbf_status["progress"] = {"done": 0, "total": 0}
    background.add_task(_run_av_backfill_bg)
    return {"status": "started", **_avbf_status}


@app.get("/news/backfill-av/status")
def av_backfill_status() -> dict[str, object]:
    """Poll target for the AV gap backfill: running?, last pass counts, resume state."""
    return dict(_avbf_status)


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


def _news_trend_from_history(sentiment: pd.DataFrame | None) -> dict[str, object] | None:
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
    if not settings.llm_analyst_enabled or not (settings.openrouter_api_key):
        return {
            "ticker": upper,
            "opinion": None,
            "note": "AI analyst disabled — set LLM_ANALYST_ENABLED=1 and OPEN_ROUTER_API_KEY."
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


class ExplainRequest(BaseModel):
    """Score evidence the client already has — reused so /explain never re-scores."""

    ticker: str
    recommendation: str | None = None
    conviction: int | None = None
    drivers: list[dict[str, Any]] = []
    news_label: str | None = None


@app.post("/explain")
def explain(req: ExplainRequest) -> dict[str, object]:
    """Short, beginner-friendly AI narrative of a score's drivers (opt-in, has a cost).

    Reuses the score payload the client already fetched (drivers + recommendation), so it
    makes no extra provider calls and never re-scores. Disabled unless the LLM is
    configured; degrades to ``None`` on any failure. Not advice — a plain-language read.
    """
    from stock_monitor.analyst import plain_explanation

    settings = get_settings()
    upper = req.ticker.upper()
    if not settings.llm_analyst_enabled or not (settings.openrouter_api_key):
        return {
            "ticker": upper,
            "summary": None,
            "note": "AI explainer disabled — set LLM_ANALYST_ENABLED=1 and OPEN_ROUTER_API_KEY."
        }

    summary = plain_explanation(req.model_dump(), settings)
    return {
        "ticker": upper,
        "summary": summary,
        "note": None if summary else "AI explainer unavailable (LLM call failed).",
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
                short_model=state.model_short,
                earnings_provider=state.earnings_provider,
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
            short_model=state.model_short,
            earnings_provider=state.earnings_provider,
        )
    if view is None:
        raise HTTPException(status_code=404, detail="position not found")
    return view


@app.get("/allocation")
def allocation_endpoint(
    state: StateDep,
    budget: float | None = None,
    tickers: str | None = None,
) -> dict[str, object]:
    """Deterministic capital-allocation plan (target weights + reasons).

    ``budget`` (optional, $) is the hypothetical capital to allocate; defaults to
    the current open book value. ``tickers`` (optional, comma-separated) restricts
    the plan to those names — the basket-builder "suggest split" flow; names we
    have no recent score for get a neutral placeholder and are listed under
    ``diagnostics.unscored``. Weights come from the auditable engine — never an
    LLM.
    """
    from stock_monitor.allocation.service import build_allocation_plan, plan_to_json

    _require_ready(state)
    if budget is not None and budget <= 0:
        raise HTTPException(status_code=422, detail="budget must be positive")
    restrict = [t for t in (tickers or "").split(",") if t.strip()]
    try:
        with Storage(state.db_path) as store:  # type: ignore[arg-type]
            plan, diagnostics = build_allocation_plan(
                store,
                state.price_provider,
                total_value=budget,
                restrict_tickers=restrict or None,
            )
    except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
        raise HTTPException(status_code=500, detail=f"allocation failed: {exc}") from exc
    return plan_to_json(plan, diagnostics)


@app.get("/baskets")
def list_baskets_endpoint(state: StateDep) -> dict[str, object]:
    """All joint portfolios, valued as a whole (headline P&L + contributions)."""
    from stock_monitor.baskets import basket_view

    if not state.db_path:
        return {"baskets": []}
    views: list[dict] = []
    with Storage(state.db_path) as store:
        for basket in store.list_baskets():
            items = store.list_basket_items(basket["id"])
            try:
                views.append(
                    basket_view({**basket, "items": items}, state.price_provider)  # type: ignore[arg-type]
                )
            except Exception:  # noqa: BLE001 — one stale basket must not hide the rest
                views.append({**basket, "legs": [], "complete": False})
    return {"baskets": views}


@app.post("/baskets")
def create_basket_endpoint(request: Request, state: StateDep) -> dict[str, object]:
    """Create a joint portfolio: total budget split across tickers by percentage."""
    from stock_monitor.baskets import BasketError, create_basket

    _require_ready(state)
    body = request.query_params  # tickers/pcts via query: easy, form-friendly
    name = body.get("name", "")
    try:
        total_budget = float(body.get("budget", "0"))
        tickers = [t for t in (body.get("tickers") or "").split(",") if t.strip()]
        pcts = [float(p) for p in (body.get("pcts") or "").split(",") if p.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"bad numbers: {exc}") from exc
    if len(tickers) != len(pcts):
        raise HTTPException(
            status_code=422, detail="tickers and pcts must have the same length"
        )
    try:
        with Storage(state.db_path) as store:  # type: ignore[arg-type]
            return create_basket(
                name or "Joint portfolio",
                total_budget,
                tickers,
                pcts,
                price_provider=state.price_provider,  # type: ignore[arg-type]
                storage=store,
            )
    except BasketError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/baskets/{basket_id}/close")
def close_basket_endpoint(basket_id: str, state: StateDep) -> dict[str, object]:
    """Close a whole joint portfolio at today's prices."""
    from stock_monitor.baskets import _quote_or_last_close

    with Storage(state.db_path) as store:  # type: ignore[arg-type]
        if store.get_basket(basket_id) is None:
            raise HTTPException(status_code=404, detail="basket not found")
        today = dt.date.today()
        for item in store.list_basket_items(basket_id):
            if item["status"] != "open":
                continue
            quote = _quote_or_last_close(item["ticker"], state.price_provider, today)  # type: ignore[arg-type]
            store.sell_basket_item(
                item["id"], dt.datetime.now(), float(quote or item["entry_price"])
            )
        store.close_basket(basket_id, dt.datetime.now())
    return {"ok": True}
