"""Edge scorecard — the plain "should I trust this with real money yet?" answer.

Fuses the two honest validation signals into one 🟢/🟡/🔴 verdict:

- **Backtest** (historical): would following the picks have beaten just buying SPY?
- **Paper** (live, forward): are the simulated buys actually beating SPY as they mature?

An edge is only *confirmed* (🟢) when **both** clear the bar. If we simply don't have
enough evidence yet (no backtest stored, or too few matured paper picks), it's 🟡
*building*. If we have evidence and it falls short, it's 🔴 *no edge yet* — keep it as a
research/second-opinion tool, not a buy button.
"""

from __future__ import annotations

from stock_monitor.paper import paper_summary
from stock_monitor.storage.db import Storage

# Bar for "there's a real edge": beat the benchmark, and win clearly more than half
# the time — across enough samples that it isn't just luck.
MIN_CLOSED_PICKS = 30
MIN_HIT_RATE = 0.55


def _backtest_signal(bt: dict | None) -> dict:
    """Grade the stored backtest: pass / fail / pending, with a plain message."""
    if bt is None:
        return {
            "status": "pending",
            "message": "No backtest run yet — it runs weekly and will appear here.",
        }
    excess = bt.get("excess_return")
    hit = bt.get("hit_rate")
    passed = (
        excess is not None and excess > 0 and hit is not None and hit >= MIN_HIT_RATE
    )
    if passed:
        msg = (
            f"Beat SPY by {excess:+.0%} over {bt.get('n_periods', '?')} months, "
            f"winning {hit:.0%} of them."
        )
    else:
        parts = []
        if excess is not None:
            verb = "beat" if excess > 0 else "trailed"
            parts.append(f"{verb} SPY by {excess:+.0%}")
        if hit is not None:
            parts.append(f"won {hit:.0%} of months (need ≥{MIN_HIT_RATE:.0%})")
        msg = "Historically " + ", ".join(parts) + "." if parts else "Not enough history."
    return {"status": "pass" if passed else "fail", "message": msg, **bt}


def _paper_signal(paper: dict) -> dict:
    """Grade the live paper track record: pass / fail / pending, with a plain message."""
    closed = paper.get("closed") or 0
    hit = paper.get("hit_rate")
    avg_excess = paper.get("avg_excess_return")
    progress = round(min(closed / MIN_CLOSED_PICKS, 1.0), 2)

    if closed < MIN_CLOSED_PICKS:
        status = "pending"
        message = (
            f"{closed} of {MIN_CLOSED_PICKS} matured picks needed before this counts — "
            "let it keep running."
        )
    else:
        passed = (
            hit is not None and hit >= MIN_HIT_RATE
            and avg_excess is not None and avg_excess > 0
        )
        status = "pass" if passed else "fail"
        message = (
            f"Beating SPY on {hit:.0%} of {closed} matured picks "
            f"(avg {avg_excess:+.1%} vs SPY)."
            if hit is not None and avg_excess is not None
            else f"{closed} matured picks recorded."
        )
    return {
        "status": status,
        "message": message,
        "closed": closed,
        "open": paper.get("open") or 0,
        "hit_rate": hit,
        "avg_excess_return": avg_excess,
        "progress": progress,
    }


def build_scorecard(storage: Storage) -> dict:
    """Return the combined edge scorecard: two graded signals + one overall verdict."""
    backtest = _backtest_signal(storage.latest_backtest())
    paper = _paper_signal(paper_summary(storage))
    statuses = {backtest["status"], paper["status"]}

    if backtest["status"] == "pass" and paper["status"] == "pass":
        verdict, label = "confirmed", "Edge confirmed"
        message = (
            "Both the backtest and live paper results beat the market. Reasonable to "
            "trust its picks — still your call, still human-in-the-loop."
        )
    elif "fail" in statuses:
        verdict, label = "no_edge", "No edge yet"
        message = (
            "The evidence doesn't beat just buying an index fund yet. Use it as a "
            "research/second-opinion tool — keep real money in your index plan."
        )
    else:
        verdict, label = "building", "Still gathering evidence"
        message = (
            "Not enough validated results yet. Keep using it to learn while the paper "
            "track record builds."
        )

    return {
        "verdict": verdict,
        "verdict_label": label,
        "message": message,
        "thresholds": {"min_closed_picks": MIN_CLOSED_PICKS, "min_hit_rate": MIN_HIT_RATE},
        "backtest": backtest,
        "paper": paper,
    }
