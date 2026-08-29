"""Pure math for the options-skew map.

No network, no storage — just the math so every part of the pipeline uses
identically computed values (engine, tests, verification).

The skew model (per 'Build Your Own Skew Map' by berttrading):

    Raw Skew        = 25d Put IV - 25d Call IV  (in vol points)
    Normalized Skew = (25d Put IV - 25d Call IV) / ATM IV

where the put and call are equidistant in *probability* (25-delta), not in
price or percent. Positive = puts richer (protection bid); negative = calls
richer (upside bid).

The 4 Quadrants:
    - Contrarian Bid: Down (1M Return < 0) + Calls Bid (Skew < 0)
    - Chase:          Up   (1M Return >= 0) + Calls Bid (Skew < 0)
    - Hedged Rally:   Up   (1M Return >= 0) + Puts Bid  (Skew >= 0)
    - Fear:           Down (1M Return < 0) + Puts Bid  (Skew >= 0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _erf(x: float) -> float:
    """Abramowitz-Stegun 7.1.26 approximation (|err| <= ~1.5e-7)."""
    if x == 0.0:
        return 0.0
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = (
        1.0
        - (
            ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
            + 0.254829592
        )
        * t
        * math.exp(-ax * ax)
    )
    return sign * y


def normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function (accurate to ~1e-7)."""
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))


def bs_price(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    vol: float,
    *,
    is_call: bool,
) -> float:
    """Black-Scholes price for a European option."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if t <= 0.0 or vol <= 0.0:
        return max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    sq = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * t) / sq
    d2 = d1 - sq
    if is_call:
        return spot * math.exp(-q * t) * normal_cdf(d1) - strike * math.exp(-r * t) * normal_cdf(d2)
    return strike * math.exp(-r * t) * normal_cdf(-d2) - spot * math.exp(-q * t) * normal_cdf(-d1)


def delta_from_iv(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    vol: float,
    *,
    is_call: bool,
) -> float:
    """Black-Scholes delta from IV."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if t <= 0.0 or vol <= 0.0:
        if is_call:
            return 1.0 if spot >= strike else 0.0
        return -1.0 if spot < strike else 0.0
    sq = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * vol * vol) * t) / sq
    if is_call:
        return math.exp(-q * t) * normal_cdf(d1)
    return math.exp(-q * t) * (normal_cdf(d1) - 1.0)


def find_atm_iv(
    strikes: list[float],
    call_ivs: dict[float, float],
    put_ivs: dict[float, float],
    spot: float,
) -> float | None:
    """Find ATM IV from strikes closest to spot price."""
    valid_strikes = [
        k for k in strikes
        if (k in call_ivs and call_ivs[k] > 0) or (k in put_ivs and put_ivs[k] > 0)
    ]
    if not valid_strikes or spot <= 0:
        return None

    closest = min(valid_strikes, key=lambda k: abs(k - spot))
    c_iv = call_ivs.get(closest)
    p_iv = put_ivs.get(closest)

    if c_iv is not None and p_iv is not None and c_iv > 0 and p_iv > 0:
        return 0.5 * (c_iv + p_iv)
    if c_iv is not None and c_iv > 0:
        return c_iv
    if p_iv is not None and p_iv > 0:
        return p_iv
    return None


def interpolate_target_delta_iv(
    strike_deltas_ivs: list[tuple[float, float, float]],
    target_abs_delta: float = 0.25,
) -> tuple[float, float] | None:
    """Interpolate strike and IV at target absolute delta from a list of (strike, abs_delta, iv).

    Expects list sorted by strike. Returns (interpolated_strike, interpolated_iv) or None.
    """
    if not strike_deltas_ivs:
        return None

    valid = [item for item in strike_deltas_ivs if item[2] > 0]
    if not valid:
        return None

    if len(valid) == 1:
        return valid[0][0], valid[0][2]

    for i in range(len(valid) - 1):
        k1, d1, iv1 = valid[i]
        k2, d2, iv2 = valid[i + 1]

        if (d1 <= target_abs_delta <= d2) or (d2 <= target_abs_delta <= d1):
            if abs(d2 - d1) < 1e-6:
                return 0.5 * (k1 + k2), 0.5 * (iv1 + iv2)
            frac = (target_abs_delta - d1) / (d2 - d1)
            target_k = k1 + frac * (k2 - k1)
            target_iv = iv1 + frac * (iv2 - iv1)
            return target_k, target_iv

    closest = min(valid, key=lambda item: abs(item[1] - target_abs_delta))
    return closest[0], closest[2]


def find_25d_legs(
    strikes: list[float],
    call_ivs: dict[float, float],
    put_ivs: dict[float, float],
    spot: float,
    t: float,
    r: float = 0.045,
    q: float = 0.0,
    target_delta: float = 0.25,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Find (strike, iv) for 25d Call and 25d Put.

    Returns ((call_25d_strike, call_25d_iv), (put_25d_strike, put_25d_iv)).
    """
    sorted_strikes = sorted(set(strikes))
    calls_data: list[tuple[float, float, float]] = []
    puts_data: list[tuple[float, float, float]] = []

    for k in sorted_strikes:
        if k in call_ivs and call_ivs[k] > 0:
            c_delta = delta_from_iv(spot, k, t, r, q, call_ivs[k], is_call=True)
            calls_data.append((k, abs(c_delta), call_ivs[k]))
        if k in put_ivs and put_ivs[k] > 0:
            p_delta = delta_from_iv(spot, k, t, r, q, put_ivs[k], is_call=False)
            puts_data.append((k, abs(p_delta), put_ivs[k]))

    call_res = interpolate_target_delta_iv(calls_data, target_delta)
    put_res = interpolate_target_delta_iv(puts_data, target_delta)
    return call_res, put_res


QuadrantType = Literal["Contrarian Bid", "Chase", "Hedged Rally", "Fear"]


@dataclass(frozen=True)
class SkewMetrics:
    spot: float
    atm_iv: float
    call_25d_strike: float
    call_25d_iv: float
    put_25d_strike: float
    put_25d_iv: float
    raw_skew: float
    normalized_skew: float
    ret_1m: float
    quadrant: QuadrantType
    sanity_passed: bool
    sanity_warning: str | None = None
    thin_chain: bool = False


def classify_quadrant(ret_1m: float, normalized_skew: float) -> QuadrantType:
    """Classify into one of 4 Skew Map quadrants.

    x-axis: 1-Month Return (positive vs negative)
    y-axis: Normalized Skew (positive = puts bid, negative = calls bid)
    """
    if ret_1m < 0:
        if normalized_skew < 0:
            return "Contrarian Bid"
        return "Fear"
    else:
        if normalized_skew < 0:
            return "Chase"
        return "Hedged Rally"


def compute_skew_metrics(
    spot: float,
    strikes: list[float],
    call_ivs: dict[float, float],
    put_ivs: dict[float, float],
    dte_days: float,
    ret_1m: float,
    r: float = 0.045,
    q: float = 0.0,
    target_delta: float = 0.25,
    total_open_interest: float = 0.0,
) -> SkewMetrics | None:
    """Full computation of skew metrics and quadrant classification for one ticker.

    ``total_open_interest`` enables the Trap #4 thin-chain check: a handful of
    strikes or tiny open interest makes every number noise (show it, don't trust it).
    """
    if spot <= 0 or dte_days <= 0 or len(strikes) < 3:
        return None

    t = dte_days / 365.0
    atm_iv = find_atm_iv(strikes, call_ivs, put_ivs, spot)
    if atm_iv is None or atm_iv <= 0:
        return None

    call_leg, put_leg = find_25d_legs(
        strikes=strikes,
        call_ivs=call_ivs,
        put_ivs=put_ivs,
        spot=spot,
        t=t,
        r=r,
        q=q,
        target_delta=target_delta,
    )

    if call_leg is None or put_leg is None:
        return None

    call_k, call_iv = call_leg
    put_k, put_iv = put_leg

    raw_skew = put_iv - call_iv
    normalized_skew = raw_skew / atm_iv

    sanity_passed = True
    sanity_warning = None

    if abs(normalized_skew) > 2.0:
        sanity_passed = False
        sanity_warning = f"Extreme normalized skew ({normalized_skew:.2f}) > 2.0"
    elif atm_iv > 3.0 or atm_iv < 0.02:
        sanity_passed = False
        sanity_warning = f"ATM IV ({atm_iv:.2%}) outside realistic bounds [2%, 300%]"
    elif call_iv > 4.0 or put_iv > 4.0:
        sanity_passed = False
        sanity_warning = f"Wing IV exceeds 400% (call: {call_iv:.2%}, put: {put_iv:.2%})"

    quadrant = classify_quadrant(ret_1m, normalized_skew)

    # Trap #4: thin chain — few usable strikes or tiny open interest = noise.
    iv_strikes = [k for k in strikes if k in call_ivs or k in put_ivs]
    thin_chain = len(iv_strikes) < 6 or total_open_interest < 500

    return SkewMetrics(
        spot=spot,
        atm_iv=atm_iv,
        call_25d_strike=call_k,
        call_25d_iv=call_iv,
        put_25d_strike=put_k,
        put_25d_iv=put_iv,
        raw_skew=raw_skew,
        normalized_skew=normalized_skew,
        ret_1m=ret_1m,
        quadrant=quadrant,
        sanity_passed=sanity_passed,
        sanity_warning=sanity_warning,
        thin_chain=thin_chain,
    )
