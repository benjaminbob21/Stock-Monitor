"""Tiered scheduler + heartbeat (build-plan §7 Phase 3).

Runs the scans on a cadence and watches its own pulse:
- **universe scan** daily (after the close) → the ranked "buy now" list.
- **watchlist scan** hourly → a faster refresh of a smaller set.
- **heartbeat check** hourly → if no successful universe scan landed within the
  configured window, alert (a silent, dead collector is the worst failure mode).

Each job is wrapped so one failure logs + alerts but never kills the scheduler.
"""

from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from stock_monitor.config import Settings, get_settings
from stock_monitor.earnings import days_until_earnings, get_earnings_provider
from stock_monitor.notify import Notifier, get_notifier
from stock_monitor.pipeline import DEFAULT_WATCHLIST
from stock_monitor.scan import scan_job
from stock_monitor.sentiment import analyze_ticker, get_news_provider, get_sentiment_analyzer
from stock_monitor.storage.db import Storage

logger = logging.getLogger("stock_monitor.scheduler")

_EARNINGS_SOON_DAYS = 5
_ALERT_DEBOUNCE_HOURS = 24


def check_heartbeat(settings: Settings, notifier: Notifier) -> bool:
    """Alert if the last successful universe scan is stale. Returns True if healthy."""
    with Storage(settings.db_path) as storage:
        last = storage.read_last_run("universe_scan", status="ok")

    if last is None or last.get("finished_at") is None:
        notifier.send("Heartbeat", "No successful universe scan recorded yet.")
        return False

    finished = dt.datetime.fromisoformat(last["finished_at"])
    age = dt.datetime.now() - finished
    if age > dt.timedelta(hours=settings.heartbeat_max_age_hours):
        notifier.send(
            "Heartbeat — stale scan",
            f"Last successful universe scan was {age} ago "
            f"(> {settings.heartbeat_max_age_hours}h). Collector may be down.",
        )
        return False
    return True


def check_holdings_news(settings: Settings, notifier: Notifier) -> int:
    """Alert (debounced) when a held position has material negative news or earnings soon.

    Returns the number of alerts sent. This is the proactive monitoring loop: you get
    pinged about *your* holdings without watching the dashboard.
    """
    news_provider = get_news_provider(settings)
    analyzer = get_sentiment_analyzer(settings)
    earnings_provider = get_earnings_provider(settings)
    sent = 0

    with Storage(settings.db_path) as storage:
        holdings = [p for p in storage.list_positions() if p["status"] == "open"]
        for position in holdings:
            ticker = position["ticker"]
            try:
                report = analyze_ticker(
                    ticker, news_provider, analyzer, settings.news_lookback_days
                )
                if (
                    report.count
                    and report.score < settings.sentiment_negative_threshold
                    and not storage.recent_alert(ticker, "negative_news", _ALERT_DEBOUNCE_HOURS)
                ):
                    top = report.items[0].headline if report.items else ""
                    notifier.send(
                        f"⚠️ {ticker}: negative news",
                        f"Sentiment {report.score:+.2f}. {top}\nConsider reviewing this holding.",
                    )
                    storage.record_alert(ticker, "negative_news", f"{report.score:+.2f}")
                    sent += 1

                days = days_until_earnings(earnings_provider, ticker)
                if (
                    days is not None
                    and 0 <= days <= _EARNINGS_SOON_DAYS
                    and not storage.recent_alert(ticker, "earnings_soon", _ALERT_DEBOUNCE_HOURS)
                ):
                    notifier.send(
                        f"📅 {ticker}: earnings in {days}d",
                        "Expect volatility around the report.",
                    )
                    storage.record_alert(ticker, "earnings_soon", f"{days}d")
                    sent += 1
            except Exception:  # noqa: BLE001 — one holding must not break the loop
                logger.exception("holdings news check failed for %s", ticker)
    return sent


def _safe(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 — a job failure must not stop the scheduler
        logger.exception("scheduled job failed: %s", getattr(fn, "__name__", fn))


def run_paper_tracking(settings: Settings, price_provider=None) -> tuple[int, int]:
    """Log the latest scan's buy-zone names as paper picks and close matured ones.

    This is the validation loop: simulate the daily buy call, then score it vs SPY when
    the horizon matures — no real money, honest track record.
    """
    from stock_monitor.paper import evaluate_paper_picks, record_paper_picks
    from stock_monitor.providers.yfinance_provider import YFinanceProvider

    price_provider = price_provider or YFinanceProvider()
    with Storage(settings.db_path) as storage:
        ranked = storage.read_latest_opportunities(limit=1000)
        recorded = record_paper_picks(settings, ranked, price_provider, storage)
        closed = evaluate_paper_picks(settings, price_provider, storage)
    logger.info("paper tracking: %d recorded, %d closed", recorded, closed)
    return recorded, closed


def collect_daily_news(settings: Settings) -> int:
    """Pull, score, and permanently archive today's news for tracked names.

    Runs daily so we never lose a day of headlines. Free news providers only reach back
    ~1 year, but by snapshotting every day we accumulate an unbroken history in DuckDB
    that we own forever — no re-subscription needed. Writes both the daily sentiment
    aggregate (the model feature) and the raw headlines (a re-scorable archive). Returns
    the number of headline rows archived.
    """
    from stock_monitor.backfill import aggregate_daily_sentiment, articles_frame

    news_provider = get_news_provider(settings)
    analyzer = get_sentiment_analyzer(settings)
    lookback = settings.news_lookback_days

    archived = 0
    with Storage(settings.db_path) as storage:
        holdings = {
            p["ticker"] for p in storage.list_positions() if p["status"] == "open"
        }
        opportunities = {
            o["ticker"]
            for o in storage.read_latest_opportunities(limit=settings.digest_top_n)
        }
        tickers = sorted({*DEFAULT_WATCHLIST, *holdings, *opportunities})
        for ticker in tickers:
            try:
                items = news_provider.get_news(ticker, lookback)
            except Exception:  # noqa: BLE001 — one bad symbol must not abort collection
                logger.exception("daily news fetch failed for %s", ticker)
                continue
            if not items:
                continue
            daily = aggregate_daily_sentiment(
                ticker, items, analyzer, max_per_day=settings.news_backfill_max_per_day
            )
            if not daily.empty:
                storage.upsert_news_sentiment(daily)
            archive = articles_frame(ticker, items, analyzer)
            if not archive.empty:
                archived += storage.upsert_news_articles(archive)
    logger.info("daily news collection archived %d headline rows", archived)
    return archived


def send_daily_digest(settings: Settings, notifier: Notifier) -> None:
    """Send a top-N digest (with paper track record) to the daily channel (Telegram)."""
    from stock_monitor.paper import compose_digest, paper_summary

    with Storage(settings.db_path) as storage:
        ranked = storage.read_latest_opportunities(limit=settings.digest_top_n)
        summary = paper_summary(storage)
    title, body = compose_digest(ranked, summary, top_n=settings.digest_top_n)
    notifier.send(title, body)


def send_weekly_digest(settings: Settings) -> None:
    """Email the weekly digest (falls back to the default notifier if SMTP is unset)."""
    from stock_monitor.notify import get_email_notifier, get_notifier
    from stock_monitor.paper import compose_digest, paper_summary

    notifier = get_email_notifier(settings) or get_notifier(settings)
    with Storage(settings.db_path) as storage:
        ranked = storage.read_latest_opportunities(limit=settings.digest_top_n)
        summary = paper_summary(storage)
    title, body = compose_digest(ranked, summary, top_n=settings.digest_top_n)
    notifier.send(f"[Weekly] {title}", body)


def run_retrain(settings: Settings) -> None:
    """Retrain the models on the full universe (the heavy job) and hot-reload the API.

    Writes the fresh model to disk (so a restart always picks it up) and, best-effort,
    swaps it into the running API's in-memory state so scores update without a restart.
    """
    from stock_monitor.pipeline import run_training
    from stock_monitor.universe import get_universe

    result = run_training(list(get_universe()), settings=settings)
    logger.info(
        "retrain complete: %s (rows=%d, acc=%.3f)",
        result.model_version, result.rows_trained, result.train_accuracy,
    )
    try:
        from stock_monitor.api import app as api_app
        from stock_monitor.models.registry import compute_model_version, load_model

        if api_app._state is not None:
            api_app._state.model = load_model(settings.model_path)
            api_app._state.model_version = (
                compute_model_version(api_app._state.model)
                if api_app._state.model is not None
                else None
            )
            api_app._state.model_short = load_model(settings.model_path_short)
            logger.info("hot-reloaded model into API: %s", api_app._state.model_version)
    except Exception:  # noqa: BLE001 — fresh model is safely on disk regardless
        logger.exception("model hot-reload failed (new model is still on disk)")


def _add_jobs(scheduler, settings: Settings, notifier: Notifier) -> None:
    """Register the tiered jobs on any APScheduler instance."""
    scheduler.add_job(
        lambda: _safe(scan_job, settings, notifier),
        "cron",
        hour=settings.scan_hour,
        id="universe_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _safe(
            scan_job, settings, notifier, None, None, None, list(DEFAULT_WATCHLIST),
            "watchlist_scan",
        ),
        "interval",
        hours=1,
        id="watchlist_scan",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _safe(check_heartbeat, settings, notifier),
        "interval",
        hours=1,
        id="heartbeat",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _safe(check_holdings_news, settings, notifier),
        "interval",
        hours=1,
        id="holdings_news",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _safe(collect_daily_news, settings),
        "cron",
        hour=settings.news_collect_hour,
        id="daily_news",
        replace_existing=True,
    )

    def _daily_digest() -> None:
        # Snapshot today's picks as paper buys, close matured ones, then send the digest.
        _safe(run_paper_tracking, settings)
        _safe(send_daily_digest, settings, notifier)

    scheduler.add_job(
        _daily_digest,
        "cron",
        hour=settings.daily_digest_hour,
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _safe(send_weekly_digest, settings),
        "cron",
        day_of_week=settings.weekly_digest_day,
        hour=settings.weekly_digest_hour,
        id="weekly_digest",
        replace_existing=True,
    )
    if settings.retrain_weekly:
        scheduler.add_job(
            lambda: _safe(run_retrain, settings),
            "cron",
            day_of_week=settings.retrain_day_of_week,
            hour=settings.retrain_hour,
            id="weekly_retrain",
            replace_existing=True,
        )


def build_scheduler(settings: Settings, notifier: Notifier) -> BlockingScheduler:
    """A blocking scheduler for the standalone `stock-monitor-scheduler` command."""
    scheduler = BlockingScheduler()
    _add_jobs(scheduler, settings, notifier)
    return scheduler


def build_background_scheduler(
    settings: Settings, notifier: Notifier
) -> BackgroundScheduler:
    """A non-blocking scheduler to run in-process with the API (one DuckDB owner)."""
    scheduler = BackgroundScheduler()
    _add_jobs(scheduler, settings, notifier)
    return scheduler


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    notifier = get_notifier(settings)
    scheduler = build_scheduler(settings, notifier)
    print(
        f"Scheduler started — universe scan daily @ {settings.scan_hour}:00, "
        f"watchlist hourly, heartbeat + holdings-news hourly (notifier: {notifier.name}). "
        "Ctrl-C to stop."
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
