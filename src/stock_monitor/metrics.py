"""Prometheus metrics for operational observability (build-plan Phase 4).

Exposed on a **localhost-only** HTTP port so Prometheus can scrape it without
going through the public Tailscale funnel or needing the API key. Nothing secret
is exported — only operational health: scan freshness, ranking size, score
distribution, request counts, and paper-mode progress.

Two kinds of metric:
- In-process **counters/histograms** for events as they happen (scores served,
  alerts sent, request latency).
- A DuckDB-backed **collector** that reads current state at each scrape, so the
  gauges survive a process restart and always reflect what's actually stored.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable

import duckdb
from prometheus_client import REGISTRY, Counter, Histogram, start_http_server
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

logger = logging.getLogger("stock_monitor.metrics")

BUY_ZONE_THRESHOLD = 70  # capped conviction at/above this counts as a "buy-zone" name

# --- in-process event metrics -------------------------------------------------
SCORES_SERVED = Counter(
    "stock_monitor_scores_served_total",
    "On-demand /score requests served, by outcome.",
    ["outcome"],
)
SCORE_LATENCY = Histogram(
    "stock_monitor_score_latency_seconds",
    "Latency of on-demand /score requests in seconds.",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
ALERTS_SENT = Counter(
    "stock_monitor_alerts_sent_total",
    "Notifier alerts emitted, by kind.",
    ["kind"],
)


class _DuckDBStateCollector(Collector):
    """Read operational state straight from DuckDB at each scrape.

    Every query is wrapped defensively: metrics must never crash the app or fail a
    scrape, and tables may not exist yet on a fresh database.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def collect(self) -> Iterable[Metric]:
        try:
            con = duckdb.connect(self._db_path, read_only=True)
        except Exception:  # noqa: BLE001 - a locked/missing DB must not break scraping
            return
        try:
            yield from self._opportunities(con)
            yield from self._scan_freshness(con)
            yield from self._paper(con)
        finally:
            con.close()

    def _opportunities(self, con: duckdb.DuckDBPyConnection) -> Iterable[Metric]:
        try:
            row = con.execute(
                "select count(*), max(capped_conviction), avg(capped_conviction), "
                f"sum(case when capped_conviction >= {BUY_ZONE_THRESHOLD} then 1 else 0 end) "
                "from opportunities"
            ).fetchone()
        except Exception:  # noqa: BLE001
            return
        if row is None:
            return
        count, mx, avg, buy = row
        total = GaugeMetricFamily(
            "stock_monitor_opportunities_total",
            "Number of names in the latest ranking.",
        )
        total.add_metric([], float(count or 0))
        yield total

        buy_zone = GaugeMetricFamily(
            "stock_monitor_buy_zone_total",
            f"Names in the latest ranking at or above the buy threshold ({BUY_ZONE_THRESHOLD}).",
        )
        buy_zone.add_metric([], float(buy or 0))
        yield buy_zone

        conviction = GaugeMetricFamily(
            "stock_monitor_conviction",
            "Capped-conviction distribution in the latest ranking.",
            labels=["stat"],
        )
        conviction.add_metric(["max"], float(mx or 0))
        conviction.add_metric(["avg"], float(avg or 0))
        yield conviction

    def _scan_freshness(self, con: duckdb.DuckDBPyConnection) -> Iterable[Metric]:
        try:
            row = con.execute(
                "select max(finished_at) from runs "
                "where job = 'universe_scan' and status = 'ok'"
            ).fetchone()
        except Exception:  # noqa: BLE001
            return
        if row is None or row[0] is None:
            return
        age = (dt.datetime.now() - row[0]).total_seconds()
        gauge = GaugeMetricFamily(
            "stock_monitor_last_scan_age_seconds",
            "Seconds since the last successful universe scan finished.",
        )
        gauge.add_metric([], float(age))
        yield gauge

    def _paper(self, con: duckdb.DuckDBPyConnection) -> Iterable[Metric]:
        try:
            row = con.execute(
                "select sum(case when status = 'open' then 1 else 0 end), "
                "sum(case when status = 'closed' then 1 else 0 end) from paper_picks"
            ).fetchone()
        except Exception:  # noqa: BLE001
            return
        if row is None:
            return
        open_n, closed_n = row
        gauge = GaugeMetricFamily(
            "stock_monitor_paper_picks",
            "Paper-mode picks by status.",
            labels=["status"],
        )
        gauge.add_metric(["open"], float(open_n or 0))
        gauge.add_metric(["closed"], float(closed_n or 0))
        yield gauge


_started = False


def start_metrics_server(port: int, db_path: str | None, addr: str = "127.0.0.1") -> None:
    """Start a localhost-only Prometheus endpoint. Idempotent (safe to call once)."""
    global _started
    if _started:
        return
    if db_path:
        REGISTRY.register(_DuckDBStateCollector(db_path))
    start_http_server(port, addr=addr)
    _started = True
    logger.info("metrics server on http://%s:%d/metrics", addr, port)
