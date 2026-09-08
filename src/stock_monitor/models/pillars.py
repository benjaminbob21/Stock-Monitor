"""Five-pillar investment breakdown for transparent conviction scoring.

Groups the 11 model features into 5 investment pillars (Momentum, Value,
Quality, Technical, Sentiment) and derives SHAP-weighted sub-scores per
pillar. Each pillar's score is rescaled from its historical SHAP range
(computed at training time) to a 0-100 scale.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import shap

from stock_monitor.features.builder import FEATURE_COLUMNS

# Mapping from pillar name to constituent feature columns.
PILLAR_MAP: dict[str, list[str]] = {
    "Momentum": ["mom_12_1", "mom_6_1"],
    "Value": ["earnings_yield", "fcf_yield"],
    "Quality": ["roe", "debt_ratio", "profit_margin"],
    "Technical": ["rsi_14", "trend_200", "vol_3m"],
    "Sentiment": ["sentiment"],
}

# Reverse map: feature → pillar name.
FEATURE_TO_PILLAR: dict[str, str] = {
    feat: pillar for pillar, feats in PILLAR_MAP.items() for feat in feats
}


@dataclass(frozen=True)
class PillarScore:
    """A single pillar's SHAP-weighted sub-score with its constituent drivers."""

    name: str
    score: int  # 0-100
    shap_sum: float  # raw sum of constituent SHAP values
    features: list[dict]  # [{feature, value, shap, direction}, ...]


def compute_pillar_scores(
    drivers: list,
    shap_ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, dict]:
    """Convert raw SHAP drivers into 0-100 pillar sub-scores.

    Each pillar's sub-score = rescaled sum of its constituent SHAP values,
    mapped from [historical_min, historical_max] → [0, 100].
    When ``shap_ranges`` is None (before first retrain), uses a symmetric
    fallback range of [-0.5, 0.5].
    """
    driver_map = {d.feature: d for d in drivers}

    pillars: dict[str, dict] = {}
    for pillar_name, feature_list in PILLAR_MAP.items():
        pillar_drivers = [driver_map[f] for f in feature_list if f in driver_map]
        total_shap = sum(d.shap for d in pillar_drivers)

        if shap_ranges and pillar_name in shap_ranges:
            lo, hi = shap_ranges[pillar_name]
        else:
            lo, hi = -0.5, 0.5  # symmetric fallback

        # Clip and rescale to 0-100
        if hi > lo:
            normalized = (total_shap - lo) / (hi - lo)
        else:
            normalized = 0.5
        score = int(round(max(0, min(100, normalized * 100))))

        pillars[pillar_name] = {
            "score": score,
            "shap_sum": round(total_shap, 4),
            "features": [
                {
                    "feature": d.feature,
                    "value": d.value,
                    "shap": d.shap,
                    "direction": d.direction,
                }
                for d in pillar_drivers
            ],
        }

    return pillars


def compute_shap_ranges(
    base_model,
    x: pd.DataFrame,
    sample_size: int = 2000,
) -> dict[str, tuple[float, float]]:
    """Compute per-pillar SHAP value ranges from the training data.

    Runs SHAP TreeExplainer on a sample of the training data and computes
    the 2nd-98th percentile range of summed SHAP values per pillar. These
    ranges are stored with the model so pillar sub-scores stay stable
    across scoring runs.
    """
    import warnings

    # Sample if dataset is large
    if len(x) > sample_size:
        x_sample = x.sample(n=sample_size, random_state=42)
    else:
        x_sample = x

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        explainer = shap.TreeExplainer(base_model)
        shap_values = explainer.shap_values(x_sample)

    # Handle SHAP output formats
    if isinstance(shap_values, list):
        shap_values = shap_values[1]  # positive class
    shap_values = np.asarray(shap_values)
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, -1]

    columns = list(x_sample.columns)
    col_idx = {c: i for i, c in enumerate(columns)}

    ranges: dict[str, tuple[float, float]] = {}
    for pillar_name, feature_list in PILLAR_MAP.items():
        idxs = [col_idx[f] for f in feature_list if f in col_idx]
        if not idxs:
            ranges[pillar_name] = (-0.5, 0.5)
            continue
        pillar_sums = shap_values[:, idxs].sum(axis=1)
        lo = float(np.percentile(pillar_sums, 2))
        hi = float(np.percentile(pillar_sums, 98))
        # Ensure minimum spread
        if hi - lo < 0.01:
            lo, hi = -0.5, 0.5
        ranges[pillar_name] = (lo, hi)

    return ranges
