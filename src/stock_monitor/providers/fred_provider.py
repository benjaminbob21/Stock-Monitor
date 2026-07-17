"""FRED / ALFRED macro-data provider — point-in-time economic series.

Macro regime (rates, inflation, the yield curve, credit stress) conditions which
factors work, so the model gets a handful of macro features. The catch is look-ahead:
today's "final" CPI for a past month was not knowable back then — it was released weeks
later and revised repeatedly. FRED's ALFRED interface exposes every *vintage* (each
value plus the ``realtime_start`` date it became public), so we can reconstruct exactly
what was knowable on any historical ``as_of`` and never leak the future into a label.

Free tier: a FRED API key (no cost) with a generous ~120 req/min limit and no daily
cap. We pull a handful of series, so cost is negligible.
"""

from __future__ import annotations

import datetime as dt
from typing import NamedTuple

from tenacity import retry, stop_after_attempt, wait_exponential

_URL = "https://api.stlouisfed.org/fred/series/observations"
# FRED's earliest allowed realtime date; paired with a far-future end this returns the
# full vintage history (one row per (observation, revision) with its realtime window).
_REALTIME_MIN = "1776-07-04"
_REALTIME_MAX = "9999-12-31"


class MacroObs(NamedTuple):
    """One observation vintage: the value and the date it first became public."""

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


class FredProvider:
    """Fetches point-in-time (vintage-aware) observations from FRED/ALFRED."""

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
    def get_series_vintages(
        self,
        series_id: str,
        *,
        units: str = "lin",
        observation_start: dt.date | None = None,
    ) -> list[MacroObs]:
        """Return every vintage of ``series_id`` as ``MacroObs`` (PIT reconstruction).

        ``units`` is a FRED transform code (``lin`` = level, ``pc1`` = percent change
        from a year ago). Missing values (FRED encodes them as ``"."``) are skipped.
        Raises :class:`FredError` on an error payload.
        """
        import requests

        params: dict[str, str | int] = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "realtime_start": _REALTIME_MIN,
            "realtime_end": _REALTIME_MAX,
            "units": units,
            "sort_order": "asc",
            "limit": 100000,
        }
        if observation_start is not None:
            params["observation_start"] = observation_start.isoformat()

        resp = requests.get(_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise FredError(f"unexpected FRED response for {series_id}")
        if "error_message" in data:
            raise FredError(str(data.get("error_message")))

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
            rt_start = _parse_date(entry.get("realtime_start", ""))
            if obs_date is None or rt_start is None:
                continue
            out.append(MacroObs(obs_date=obs_date, realtime_start=rt_start, value=value))
        return out
