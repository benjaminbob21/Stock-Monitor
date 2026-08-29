"""LLM portfolio brief: one narration call over engine-owned numbers.

Two-layer principle (see allocation/): the deterministic engine produces the
weights and the score model owns every conviction; the LLM only narrates the
already-computed plan and may highlight disagreements — it never generates
numbers. The brief is cached per calendar day so refreshing the page costs
nothing; the review endpoint is a per-stock second opinion with a 1-per-hour
cache to cap token spend.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from stock_monitor.allocation.service import build_allocation_plan

logger = logging.getLogger(__name__)

_BRIEF_SYSTEM_PROMPT = (
    "You are the narrator of a personal stock-portfolio brief. You receive a "
    "deterministic allocation plan (target vs current percentages, convictions, "
    "warnings) produced by a calibrated engine, plus optional sentiment context. "
    "Write a short plain-English brief for the portfolio owner.\n"
    "Rules:\n"
    "- NEVER invent or change numbers; only use the ones given.\n"
    "- Frame every sentence as a rebalance TARGET for holdings the owner "
    "already has (or cash) — never as a suggestion to buy something new.\n"
    "- Structure: one 2-sentence overview, then up to 4 bullet points on the "
    "biggest deltas (largest |delta_pct| first), then a 1-sentence watch-out "
    "summarizing the warnings (or saying the plan is clean).\n"
    "- Plain text only: no markdown, no ** or * emphasis, no # headers.\n"
    "- Do not give trading instructions; describe what the engine suggests and "
    "why in one clause per bullet.\n"
    "- Total under 180 words. No preamble, no sign-off."
)

_REVIEW_SYSTEM_PROMPT = (
    "You are an equity analyst giving a short second opinion on a single stock. "
    "You receive the app's calibrated model score (the signal of record), its "
    "drivers, risk flags, and recent sentiment. Give a BUY/HOLD/SELL opinion as "
    "JSON: {\"opinion\": \"BUY|HOLD|SELL\", \"confidence\": \"low|medium|high\", "
    "\"rationale\": \"<=120 words\", \"key_risks\": [\"...\"]}. Weigh the model "
    "score seriously but say so if you disagree and why. Never invent numbers."
)

# Module-level caches: brief = one per calendar day; review = one per
# ticker/hour. Survives across requests in the single uvicorn process.
_brief_cache: dict[str, Any] = {}
_review_cache: dict[str, tuple[dt.datetime, dict[str, Any]]] = {}


def build_brief_context(
    store: Any,
    price_provider: object,
    total_value: float | None = None,
) -> dict[str, Any]:
    """Assemble the compact JSON context the LLM narrates (engine numbers only)."""
    plan, diagnostics = build_allocation_plan(store, price_provider, total_value=total_value)

    allocations: list[dict[str, Any]] = [
        {
            "ticker": a.ticker,
            "target_pct": round(a.target_weight * 100, 1),
            "current_pct": round(a.current_weight * 100, 1),
            "delta_pct": round(a.delta_weight * 100, 1),
            "conviction": a.conviction,
            "reasons": list(a.reasons)[:3],
        }
        for a in plan.allocations
    ]
    # Narrative focuses on the biggest moves; tail positions stay in the data.
    allocations.sort(key=lambda a: abs(float(a["delta_pct"])), reverse=True)

    context: dict[str, Any] = {
        "as_of": plan.as_of.date().isoformat(),
        "total_value": round(plan.total_value, 2),
        "cash_pct": round(plan.cash_weight * 100, 1),
        "allocations": allocations,
        "warnings": list(plan.warnings),
    }
    price_errors = diagnostics.get("price_errors")
    if isinstance(price_errors, dict) and price_errors:
        context["unpriceable_positions"] = sorted(price_errors)
    return context


def portfolio_brief(
    store: Any,
    price_provider: object,
    total_value: float | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Narrate today's allocation plan. Cached per calendar day; LLM-optional.

    Returns a dict with ``brief`` (markdown-ish text), ``context`` (the engine
    numbers the LLM saw), and ``cached``/``model`` metadata. When the LLM is
    disabled or the call fails, ``brief`` is None but ``context`` still carries
    the full deterministic plan so the UI can render it.
    """
    llm_ok = bool(
        settings is not None
        and getattr(settings, "llm_analyst_enabled", False)
        and getattr(settings, "openrouter_api_key", "")
    )
    today = dt.date.today().isoformat()
    cached = _brief_cache.get(today)
    if cached is not None and cached.get("llm_available") == llm_ok:
        return {**cached, "cached": True}

    context = build_brief_context(store, price_provider, total_value=total_value)
    result: dict[str, Any] = {
        "as_of": today,
        "context": context,
        "brief": None,
        "model": None,
        "llm_available": llm_ok,
        "note": (
            None
            if llm_ok
            else "AI narration disabled — set LLM_ANALYST_ENABLED=1 and OPEN_ROUTER_API_KEY."
        ),
        "cached": False,
    }
    if llm_ok:
        text = _narrate_brief(context, settings)
        if text:
            result["brief"] = text
            result["model"] = settings.llm_model
    _brief_cache.clear()
    _brief_cache[today] = result
    return result


def _narrate_brief(context: dict[str, Any], settings: Any) -> str | None:
    import requests

    body: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": _BRIEF_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, default=str)},
        ],
    }
    try:
        resp = requests.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://stock-monitor.vercel.app",
                "X-Title": "Stock Monitor",
            },
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"]).strip() or None
    except Exception:  # noqa: BLE001 — the plan must never depend on the LLM
        logger.exception("LLM portfolio brief call failed")
        return None


def ticker_review(
    payload: dict[str, Any],
    settings: Any,
    *,
    max_per_hour: int = 1,
) -> dict[str, Any] | None:
    """Per-stock LLM review (the 1/hour-capped opt-in). None when disabled/failed.

    ``payload`` is the score payload already shown on the stock page (ticker,
    conviction, recommendation, drivers, risk_flags, news sentiment if any).
    Cached per ticker for one hour so taps and retries don't re-bill.
    """
    api_key = getattr(settings, "openrouter_api_key", "")
    if not getattr(settings, "llm_analyst_enabled", False) or not api_key:
        return None

    ticker = str(payload.get("ticker", "")).upper().strip()
    now = dt.datetime.now(dt.UTC)
    hit = _review_cache.get(ticker)
    if hit and (now - hit[0]).total_seconds() < 3600 / max_per_hour:
        return {**hit[1], "cached": True}

    import requests

    body: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ],
    }
    try:
        resp = requests.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://stock-monitor.vercel.app",
                "X-Title": "Stock Monitor",
            },
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001 — the primary score must never depend on the LLM
        logger.exception("LLM ticker review failed for %s", ticker)
        return None

    opinion = str(parsed.get("opinion", "")).upper().strip()
    if opinion not in {"BUY", "HOLD", "SELL"}:
        logger.warning("LLM review returned invalid opinion: %r", parsed.get("opinion"))
        return None

    result: dict[str, Any] = {
        "ticker": ticker,
        "opinion": opinion,
        "confidence": str(parsed.get("confidence", "")).lower() or "unknown",
        "rationale": str(parsed.get("rationale", "")).strip(),
        "key_risks": [str(r) for r in parsed.get("key_risks", []) if str(r).strip()],
        "model": settings.llm_model,
        "disclaimer": (
            "AI-generated review for a human to weigh — not advice. The calibrated "
            "model score remains the signal of record."
        ),
        "cached": False,
    }
    _review_cache[ticker] = (now, result)
    return result
