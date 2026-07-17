"""FRED / ALFRED macro-data provider — point-in-time economic series.

Macro regime (rates, inflation, the yield curve, credit stress) conditions which
factors work, so the model gets a handful of macro features. The catch is look-ahead:
today's "final" CPI for a past month was not knowable back then — it was released weeks
later and revised repeatedly. ALFRED's ``output_type=4`` ("initial release only") gives
exactly what was first published for each observation, plus the ``realtime_start`` date
it became public, so we can reconstruct what was knowable on any historical ``as_of``.

FRED caps a single request at 2000 distinct vintage dates, which a multi-decade *daily*
series blows past, so we page the real-time window in ~5-year chunks and merge. Free
tier: a FRED API key (no cost), ~120 req/min, no daily cap.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, NamedTuple

from tenacity import retry, stop_after_attempt, wait_exponential

_URL = "https://api.stlouisfed.org/fred/series/observations"
_REALTIME_MAX = "9999-12-31"  # ALFRED's "last available" sentinel (future-open window)
_DEFAULT_START = dt.date(2000, 1, 1)  # earliest real-time chunk; covers any training window
_CHUNK_YEARS = 5  # keep each daily chunk under FRED's 2000-vintage-date cap


class MacroObs(NamedTuple):
    """One observation's initial release: the value and the date it became public."""

    obs_date: dt.date
    realtime_start: dt.date
    value: float


class FredError(RuntimeError):
    """Raised when FRED returns an error payload (bad key, unknown series, throttle)."""


def _parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _realtime_windows(start: dt.date) -> list[tuple[str, str]]:
    """Split ``[start, today]`` into ~5-year real-time windows (last one future-open).

    Chunking keeps each daily-series request under FRED's 2000-vintage-date limit; the
    final window ends at the ``9999-12-31`` sentinel so newly released data is included.
    """
    today = dt.date.today()
    bounds: list[dt.date] = []
    year = start.year
    while year <= today.year:
        bounds.append(dt.date(year, 1, 1))
        year += _CHUNK_YEARS
    windows = [
        (bounds[i].isoformat(), bounds[i + 1].isoformat())
        for i in range(len(bounds) - 1)
    ]
    windows.append((bounds[-1].isoformat(), _REALTIME_MAX))
    return windows


class FredProvider:
    """Fetches point-in-time (initial-release) observations from FRED/ALFRED."""

    name = "fred"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("FredProvider requires an API key")
        self._api_key = api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        reraise=True,
    )
    def _fetch_window(self, series_id: str, rt_start: str, rt_end: str) -> list[MacroObs]:
        import requests

        params: dict[str, str | int] = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "output_type": 4,  # initial release only (what was first knowable)
            "units": "lin",  # output_type=4 requires lin; transforms are applied by us
            "realtime_start": rt_start,
            "realtime_end": rt_end,
            "sort_order": "asc",
            "limit": 100000,
        }
        resp = requests.get(_URL, params=params, timeout=30)
        data: Any = None
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise FredError(f"non-JSON response for {series_id}") from None
        if isinstance(data, dict) and data.get("error_message"):
            raise FredError(str(data["error_message"]))
        resp.raise_for_status()

        out: list[MacroObs] = []
        for entry in data.get("observations") or []:
            raw = entry.get("value")
            if raw is None or raw == ".":  # FRED's missing-value sentinel
                continue
            try:
                value = float(raw)
            except (ValueError, TypeError):
                continue
            obs_date = _parse_date(entry.get("date", ""))
            rt = _parse_date(entry.get("realtime_start", ""))
            if obs_date is None or rt is None:
                continue
            out.append(MacroObs(obs_date=obs_date, realtime_start=rt, value=value))
        return out

    def get_series_vintages(
        self, series_id: str, *, observation_start: dt.date | None = None
    ) -> list[MacroObs]:
        """Return each observation's initial release for ``series_id`` (PIT-safe).

        Pages the real-time window in chunks (FRED's 2000-vintage-date cap) and merges,
        keeping the earliest release per observation date. Sorted by observation date.
        Raises :class:`FredError` on an error payload.
        """
        start = observation_start or _DEFAULT_START
        best: dict[dt.date, MacroObs] = {}
        for rt_start, rt_end in _realtime_windows(start):
            try:
                window_obs = self._fetch_window(series_id, rt_start, rt_end)
            except FredError as exc:
                # Some series (esp. FRED-computed spreads) only entered ALFRED partway
                # through history; early windows return "does not exist in ALFRED".
                # Skip those and keep the windows that do have vintages.
                if "does not exist in alfred" in str(exc).lower():
                    continue
                raise
            for obs in window_obs:
                current = best.get(obs.obs_date)
                if current is None or obs.realtime_start < current.realtime_start:
                    best[obs.obs_date] = obs
        return sorted(best.values(), key=lambda o: o.obs_date)
