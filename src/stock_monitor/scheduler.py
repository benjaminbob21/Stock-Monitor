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
