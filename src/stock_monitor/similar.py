"""Learn from history: find past setups that look like today's and how they played out.

The trained model gives a score; this adds *evidence by analogy*. Given today's feature
row for a ticker, it finds the most similar historical setups in the local point-in-time
feature store (across every ticker, not just this one) and reports how many of those
analogous setups went on to beat the benchmark. That empirical base rate — "setups that
looked like this beat SPY 7 of 10 times" — is a transparent, $0, no-lookahead second
signal that raises (or lowers) confidence by confluence with the real past.

This is deliberately local and simple: standardised Euclidean nearest-neighbours over the
same PIT features the model trains on. No external vector DB, no paid data, no lookahead —
only labelled (matured) history is eligible, so an analog always has a known outcome.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from stock_monitor.features.builder import FEATURE_COLUMNS


def find_similar_setups(
    target: dict,
    history: pd.DataFrame,
    *,
    k: int = 5,
) -> dict:
    """Return the ``k`` most similar labelled historical setups and their base rate.

    ``target`` is a feature row (dict with FEATURE_COLUMNS, plus ``ticker``/``as_of``).
    ``history`` is the stored feature table (FEATURE_COLUMNS + ``label`` + ``ticker`` +
    ``as_of``). Only rows with a known (non-null) label are eligible — a matured outcome.
    """
    empty: dict[str, object] = {
        "k": k,
        "n_history": 0,
        "base_rate": None,
        "overall_base_rate": None,
        "analogs": [],
    }
    if history is None or history.empty:
        return empty

    feats = [
        c for c in FEATURE_COLUMNS
        if c in target and pd.notna(target.get(c))
    ]
    if not feats:
        return empty

    labelled = history[history["label"].notna()].copy()
    if labelled.empty:
        return empty

    # Drop the target's own row (same ticker + as_of) so it can't match itself.
    t_ticker = str(target.get("ticker", "")).upper()
    t_as_of = target.get("as_of")
    if t_as_of is not None:
        t_date = t_as_of if isinstance(t_as_of, dt.date) else pd.to_datetime(t_as_of).date()
        as_of_dates = pd.to_datetime(labelled["as_of"]).dt.date
        labelled = labelled[
            ~((labelled["ticker"].str.upper() == t_ticker) & (as_of_dates == t_date))
        ]
    if labelled.empty:
        return empty

    x = labelled[feats].astype(float)
    means = x.mean()
    stds = x.std(ddof=0).replace(0.0, 1.0)
    xz = ((x - means) / stds).fillna(0.0)

    t_vec = pd.Series({c: float(target[c]) for c in feats})
    tz = ((t_vec - means) / stds).fillna(0.0)

    distances = np.sqrt(((xz.to_numpy() - tz.to_numpy()) ** 2).sum(axis=1))
    labelled = labelled.assign(distance=distances)

    top = labelled.nsmallest(k, "distance")
    base_rate = float(top["label"].astype(int).mean()) if not top.empty else None
    overall = float(labelled["label"].astype(int).mean())

    analogs = [
        {
            "ticker": str(row.ticker).upper(),
            "as_of": (
                pd.to_datetime(row.as_of).date().isoformat() if row.as_of is not None else None
            ),
            "distance": round(float(row.distance), 3),
            "beat_benchmark": bool(int(row.label)),
        }
        for row in top.itertuples(index=False)
    ]
    return {
        "k": k,
        "n_history": int(len(labelled)),
        "base_rate": round(base_rate, 3) if base_rate is not None else None,
        "overall_base_rate": round(overall, 3),
        "analogs": analogs,
    }
