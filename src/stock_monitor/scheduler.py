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

from apscheduler.schedulers.blocking import BlockingScheduler

from stock_monitor.config import Settings, get_settings
from stock_monitor.notify import Notifier, get_notifier
from stock_monitor.pipeline import DEFAULT_WATCHLIST
from stock_monitor.scan import scan_job
from stock_monitor.storage.db import Storage

logger = logging.getLogger("stock_monitor.scheduler")


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


def _safe(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 — a job failure must not stop the scheduler
        logger.exception("scheduled job failed: %s", getattr(fn, "__name__", fn))


def build_scheduler(settings: Settings, notifier: Notifier) -> BlockingScheduler:
    scheduler = BlockingScheduler()
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
    return scheduler


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    notifier = get_notifier(settings)
    scheduler = build_scheduler(settings, notifier)
    print(
        f"Scheduler started — universe scan daily @ {settings.scan_hour}:00, "
        f"watchlist hourly, heartbeat hourly (notifier: {notifier.name}). Ctrl-C to stop."
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
