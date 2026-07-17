"""Macro/regime features — FRED series, PIT lookup, and backfill.

The market's regime (the shape of the yield curve, the level of rates, inflation, and
credit stress) changes *which* signals work — momentum in an easing cycle behaves
nothing like momentum in a hiking one. These five series give the model that context.

Point-in-time correctness (no look-ahead): FRED/ALFRED returns every vintage of each
series with the ``realtime_start`` date it became public. We reduce each series to its
*first-release* value per observation date (the number actually knowable at the time),
then :func:`make_macro_lookup` answers "what was the freshest reading available on
``as_of``?" — never a later revision, never a future release.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_right
from collections.abc import Callable
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from stock_monitor.providers.fred_provider import MacroObs

# feature column -> FRED series + transform. ALFRED's initial-release output requires
# lin units, so macro_cpi_yoy is derived here as year-over-year % change of the CPI
# index (from first-release values, keeping it point-in-time). The rest are levels.
MACRO_SERIES: dict[str, dict[str, str]] = {
    "macro_yield_curve": {"series_id": "T10Y2Y", "transform": "level"},
    "macro_fed_funds": {"series_id": "DFF", "transform": "level"},
    "macro_cpi_yoy": {"series_id": "CPIAUCSL", "transform": "yoy"},
    "macro_unemployment": {"series_id": "UNRATE", "transform": "level"},
    "macro_credit_spread": {"series_id": "BAA10Y", "transform": "level"},
}

MACRO_FEATURE_COLUMNS: tuple[str, ...] = tuple(MACRO_SERIES)

_SERIES_TO_COLUMN: dict[str, str] = {
    spec["series_id"]: col for col, spec in MACRO_SERIES.items()
}


def make_macro_lookup(
    macro: pd.DataFrame,
) -> Callable[[dt.date], dict[str, float]]:
    """Build a PIT macro lookup from stored vintages.

    ``macro`` has columns ``series_id``, ``obs_date``, ``realtime_start``, ``value``
    (as stored by :func:`backfill_macro`). The returned callable maps an ``as_of`` date
    to ``{feature_column: value}`` using only the first-release value of the most recent
    observation whose release date is on or before ``as_of`` — so it never peeks ahead.
    Columns with no data available at ``as_of`` are omitted (left as NaN downstream).
    """
    per_column: dict[str, tuple[list[dt.date], list[float]]] = {}
    if macro is not None and not macro.empty:
        frame = macro.copy()
        frame["obs_date"] = pd.to_datetime(frame["obs_date"]).dt.date
        frame["realtime_start"] = pd.to_datetime(frame["realtime_start"]).dt.date
        for series_id, group in frame.groupby("series_id"):
            column = _SERIES_TO_COLUMN.get(str(series_id))
            if column is None:
                continue
            # First release per observation: the value actually knowable at the time.
            first = (
                group.sort_values("realtime_start")
                .drop_duplicates(subset=["obs_date"], keep="first")
                .sort_values("realtime_start")
            )
            releases: list[dt.date] = []
            running: list[float] = []
            best_obs: dt.date | None = None
            best_val = float("nan")
            for row in first.itertuples(index=False):
                if best_obs is None or row.obs_date >= best_obs:
                    best_obs = row.obs_date
                    best_val = float(row.value)
                releases.append(row.realtime_start)
                running.append(best_val)
            if releases:
                per_column[column] = (releases, running)

    def lookup(as_of: dt.date) -> dict[str, float]:
        result: dict[str, float] = {}
        for column, (releases, running) in per_column.items():
            idx = bisect_right(releases, as_of) - 1
            if idx >= 0:
                result[column] = running[idx]
        return result

    return lookup


def backfill_macro(
    provider: object,
    storage: object,
    *,
    observation_start: dt.date | None = None,
) -> int:
    """Fetch every :data:`MACRO_SERIES` from FRED and upsert its initial-release values.

    Idempotent: re-running refreshes values and adds newly released observations (keyed
    by series + observation date + realtime_start). CPI is stored as year-over-year %
    change derived from the first-release index. Returns the row count stored.
    """
    rows: list[dict[str, object]] = []
    for spec in MACRO_SERIES.values():
        series_id = spec["series_id"]
        observations = provider.get_series_vintages(  # type: ignore[attr-defined]
            series_id, observation_start=observation_start
        )
        if spec["transform"] == "yoy":
            observations = _to_yoy(observations)
        for obs in observations:
            rows.append(
                {
                    "series_id": series_id,
                    "obs_date": obs.obs_date,
                    "realtime_start": obs.realtime_start,
                    "value": obs.value,
                }
            )
    if not rows:
        return 0
    frame = pd.DataFrame(rows)
    return storage.upsert_macro_series(frame)  # type: ignore[attr-defined]


def _to_yoy(observations: list[MacroObs]) -> list[MacroObs]:
    """Convert a first-release index series to year-over-year % change, PIT-safe.

    Each month's YoY uses the first-release index for that month and the same month a
    year earlier, and inherits the current month's release date — so it becomes knowable
    exactly when the current month's index is first published.
    """
    from stock_monitor.providers.fred_provider import MacroObs

    # First release per observation month (earliest realtime wins).
    by_month: dict[dt.date, MacroObs] = {}
    for obs in sorted(observations, key=lambda o: o.realtime_start):
        by_month.setdefault(obs.obs_date, obs)

    out: list[MacroObs] = []
    for obs_date, obs in sorted(by_month.items()):
        prior_date = dt.date(obs_date.year - 1, obs_date.month, 1)
        prior = by_month.get(prior_date)
        if prior is None or prior.value == 0:
            continue
        yoy = (obs.value / prior.value - 1.0) * 100.0
        out.append(
            MacroObs(obs_date=obs_date, realtime_start=obs.realtime_start, value=yoy)
        )
    return out
