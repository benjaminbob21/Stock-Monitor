"""Deterministic capital-allocation engine (approved design 2026-08-27).

Core rule: ``weight_i ∝ conviction_i / volatility_i`` — conviction-weighted, volatility
deflated — then bounded by per-position caps/floors and a cash floor that scales with
how weak the aggregate book is. Every number is auditable: each allocation carries
human-readable ``reasons``. LLMs never touch these numbers; they only narrate them
downstream. Conviction bands intentionally mirror positions.py's language (SELL<40,
TRIM<55) so allocation never contradicts the scorecard.

All weights are fractions of total capital and every returned plan sums to exactly 1.0
(positions + cash), so the UI can render it as one honest pie.
"""

from __future__ import annotations

import datetime as dt

from stock_monitor.allocation.contracts import (
    AllocationConstraints,
    AllocationPlan,
    PortfolioState,
    PositionAllocation,
    PositionInput,
)

# Conviction bands — kept in lockstep with positions.py's sell/trim language.
SELL_BELOW = 40.0
TRIM_BELOW = 55.0

# Cash floor interpolates from 0 (book at/above this conviction) to the full
# constraints.cash_floor at or below AGGREGATE_WEAK.
AGGREGATE_WEAK = 50.0
AGGREGATE_STRONG = 65.0


# Volatility floor so tiny-vol names can't swallow the book via the 1/vol term.
_VOL_FLOOR = 0.10


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _aggregate_conviction(inputs: list[PositionInput]) -> float:
    if not inputs:
        return 0.0
    return sum(i.conviction for i in inputs) / len(inputs)


def _cash_weight(inputs: list[PositionInput], constraints: AllocationConstraints) -> float:
    """Cash floor scales linearly between weak and strong aggregate conviction."""
    if not inputs:
        return 1.0
    agg = _aggregate_conviction(inputs)
    if agg >= AGGREGATE_STRONG:
        return 0.0
    if agg <= AGGREGATE_WEAK:
        return constraints.cash_floor
    t = (agg - AGGREGATE_WEAK) / (AGGREGATE_STRONG - AGGREGATE_WEAK)
    return constraints.cash_floor * (1.0 - t)


def _sentiment_tilt(input_: PositionInput) -> float:
    """Consensus tilt in (0.9, 1.1): bearish news trims, bullish news adds, mildly.

    Deliberately small — sentiment is a hair on top of the model's conviction, not
    a second vote. Alt (Reddit/media) sentiment nudges at half strength because it
    is noisier and contrarian-leaning (euphoria often precedes pullbacks).
    """

    def _one(s: float, strength: float) -> float:
        return 1.0 + strength * _clamp(s, -1.0, 1.0)

    tilt = 1.0
    if input_.news_sentiment is not None:
        tilt *= _one(input_.news_sentiment, 0.05)
    if input_.alt_sentiment is not None:
        tilt *= _one(input_.alt_sentiment, 0.025)
    return _clamp(tilt, 0.9, 1.1)

def _raw_weight(pos: PositionInput) -> float:
    """Conviction² / volatility × sentiment tilt × risk penalty.

    Squaring conviction spreads the book toward the strongest names; the 1/vol term
    deflates jumpy tickers; both are legible in the reasons.
    """
    conviction_term = (_clamp(pos.conviction, 0.0, 100.0) / 100.0) ** 2
    vol_term = 1.0 / max(pos.volatility, _VOL_FLOOR)
    return conviction_term * vol_term * _sentiment_tilt(pos) * pos.risk_penalty


def _reasons_for(
    pos: PositionInput, weight: float, constraints: AllocationConstraints
) -> tuple[str, ...]:
    reasons: list[str] = []
    if pos.conviction < TRIM_BELOW:
        reasons.append(f"conviction {pos.conviction:.0f} is trim territory (<{TRIM_BELOW:.0f})")
    else:
        reasons.append(f"conviction {pos.conviction:.0f}")
    if pos.volatility > 0.45:
        reasons.append(f"high volatility ({pos.volatility:.0%}) deflates weight")
    elif pos.volatility < _VOL_FLOOR * 2:
        reasons.append(f"low volatility ({pos.volatility:.0%}) supports weight")
    if pos.risk_flags:
        reasons.append(f"risk-flag haircut ×{pos.risk_penalty:.2f} ({', '.join(pos.risk_flags)})")
    if pos.news_sentiment is not None and abs(pos.news_sentiment) >= 0.2:
        lean = "bearish" if pos.news_sentiment < 0 else "bullish"
        reasons.append(f"{lean} news sentiment tilt ×{_sentiment_tilt(pos):.2f}")
    if weight >= constraints.max_per_position - 1e-9:
        reasons.append(f"capped at {constraints.max_per_position:.0%}")
    return tuple(reasons)


def allocate(
    candidates: list[PositionInput],
    state: PortfolioState,
    constraints: AllocationConstraints | None = None,
    *,
    as_of: dt.datetime | None = None,
) -> AllocationPlan:
    """Compute target weights for ``candidates`` given the current portfolio state.

    Candidates are ranked by conviction; only the top ``max_positions`` survive, and
    any candidate below the SELL band is excluded outright (the model's own language).
    The plan's weights always sum to exactly 1.0 with the remainder in cash.
    """
    constraints = constraints or AllocationConstraints()
    warnings: list[str] = []
    if state.total_value <= 0:
        warnings.append("total_value must be positive; weights are still returned")

    ranked = sorted(candidates, key=lambda p: p.conviction, reverse=True)
    dropped = [p.ticker for p in ranked if p.conviction < SELL_BELOW]
    if dropped:
        warnings.append(
            "below the sell band (conviction <"
            f" {SELL_BELOW:.0f}), not allocated: {', '.join(dropped)}"
        )
    eligible = [p for p in ranked if p.conviction >= SELL_BELOW][: constraints.max_positions]
    overflow_names = len(ranked) - len(dropped) - len(eligible)
    if overflow_names > 0:
        warnings.append(
            f"{overflow_names} candidates beyond max_positions={constraints.max_positions}"
        )

    current = {t: w for t, w in state.positions}
    if not eligible:
        return AllocationPlan(
            as_of=as_of or dt.datetime.now(),
            total_value=state.total_value,
            allocations=(),
            cash_weight=1.0,
            constraints=constraints,
            warnings=tuple(warnings) or ("no eligible candidates",),
        )

    cash = _cash_weight(eligible, constraints)
    investable = 1.0 - cash
    raws = [_raw_weight(p) for p in eligible]
    total_raw = sum(raws)
    weights = [investable * r / total_raw if total_raw > 0 else 0.0 for r in raws]

    # Cap pass: overflow from capped positions returns to cash (never force-fed into
    # other names — that would silently concentrate the book).
    for i, w in enumerate(weights):
        if w > constraints.max_per_position:
            weights[i] = constraints.max_per_position
    if any(w >= constraints.max_per_position for w in weights):
        warnings.append("one or more positions hit the per-position cap; excess sits in cash")

    # Floor pass: dust allocations below min_per_position are dropped to cash —
    # sub-fractional-share noise isn't worth a line item.
    kept: list[tuple[PositionInput, float]] = []
    for pos, w in zip(eligible, weights, strict=True):
        if 0 < w < constraints.min_per_position:
            warnings.append(
                f"{pos.ticker} dropped: weight {w:.1%} under the"
                f" {constraints.min_per_position:.0%} floor"
            )
        else:
            kept.append((pos, w))

    allocations = tuple(
        PositionAllocation(
            ticker=pos.ticker,
            target_weight=round(w, 6),
            current_weight=round(current.get(pos.ticker, 0.0), 6),
            conviction=pos.conviction,
            reasons=_reasons_for(pos, w, constraints),
        )
        for pos, w in kept
    )
    cash = round(1.0 - sum(a.target_weight for a in allocations), 6)
    return AllocationPlan(
        as_of=as_of or dt.datetime.now(),
        total_value=state.total_value,
        allocations=allocations,
        cash_weight=cash,
        constraints=constraints,
        warnings=tuple(warnings),
    )
