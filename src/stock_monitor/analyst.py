"""LLM 'AI analyst' second opinion (optional, opt-in, per-call cost).

The quantitative model produces the primary conviction score + SHAP drivers. This
module asks a language model for an independent, plain-language *second opinion* on the
same evidence — a BUY / HOLD / SELL read with a short rationale and the risks it sees.
It is disabled unless both ``llm_analyst_enabled`` and ``openai_api_key`` are set, so it
never costs anything by accident, and any failure degrades to ``None`` (the primary
score always stands on its own).

Guardrail: this is a *second opinion for a human*, not an instruction. Nothing here
trades. The model's number remains the transparent, calibrated signal of record.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from stock_monitor.config import Settings

logger = logging.getLogger("stock_monitor.analyst")

_SYSTEM_PROMPT = (
    "You are a cautious equity-research assistant giving a SECOND OPINION on a "
    "quantitative model's output. You are NOT a financial advisor and must not tell "
    "the user to trade. Weigh, in order: (1) the model's calibrated conviction and its "
    "top SHAP drivers; (2) how SIMILAR PAST SETUPS resolved — the empirical base rate of "
    "analogous historical situations beating the benchmark (a base rate near 50% means "
    "the analogy carries little signal); (3) the TREND in news sentiment over time (is it "
    "improving, deteriorating, or flat?) and its latest reading; (4) any risk flags. Be "
    "skeptical: if the drivers are weak, the analog base rate is near 50% or thin, the "
    "news trend is deteriorating, or risk flags are present, prefer HOLD. Ground your "
    "rationale in the historical analogs and the sentiment trajectory when they are "
    "provided. Respond with STRICT JSON only, matching: {\"opinion\": "
    "\"BUY\"|\"HOLD\"|\"SELL\", \"confidence\": \"low\"|\"medium\"|\"high\", "
    "\"rationale\": string (<=60 words), \"key_risks\": [string, ...]}."
)

_VALID_OPINIONS = {"BUY", "HOLD", "SELL"}


def _build_user_message(payload: dict) -> str:
    """Compact the score payload into the evidence block for the model."""
    drivers = [
        {
            "feature": d.get("feature"),
            "direction": d.get("direction"),
            "shap": round(float(d.get("shap", 0.0)), 3),
        }
        for d in payload.get("drivers", [])
    ]
    evidence = {
        "ticker": payload.get("ticker"),
        "model_conviction_0_100": payload.get("conviction"),
        "model_recommendation": payload.get("recommendation"),
        "near_term_conviction": payload.get("near_term", {}).get("conviction")
        if isinstance(payload.get("near_term"), dict)
        else payload.get("near_term_conviction"),
        "top_drivers": drivers,
        "risk_flags": payload.get("risk_flags", []),
        "news_sentiment": payload.get("news_sentiment"),
        "news_label": payload.get("news_label"),
    }

    # Historical analogs (learn-from-history base rate) — included when available.
    similar = payload.get("similar")
    if isinstance(similar, dict) and similar.get("analogs"):
        evidence["similar_setups"] = {
            "base_rate_beat_benchmark": similar.get("base_rate"),
            "overall_base_rate": similar.get("overall_base_rate"),
            "n_history": similar.get("n_history"),
            "analogs": [
                {
                    "ticker": a.get("ticker"),
                    "as_of": a.get("as_of"),
                    "beat_benchmark": a.get("beat_benchmark"),
                }
                for a in similar.get("analogs", [])[:5]
            ],
        }

    # News-sentiment trajectory from backfilled history — included when available.
    trend = payload.get("news_trend")
    if isinstance(trend, dict) and trend.get("direction"):
        evidence["news_trend"] = trend

    return (
        "Second-opinion request. Evidence (JSON):\n"
        + json.dumps(evidence, default=str)
        + "\nReturn STRICT JSON only."
    )


def second_opinion(payload: dict, settings: Settings) -> dict | None:
    """Ask the LLM for a BUY/HOLD/SELL second opinion. Returns None when disabled/failed.

    ``payload`` is a score payload (ticker, conviction, recommendation, drivers,
    risk_flags, and optionally news_sentiment/news_label).
    """
    if not settings.llm_analyst_enabled or not settings.openai_api_key:
        return None

    import requests

    body: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(payload)},
        ],
    }
    try:
        resp = requests.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception:  # noqa: BLE001 — the primary score must never depend on the LLM
        logger.exception("LLM analyst call failed")
        return None

    opinion = str(parsed.get("opinion", "")).upper().strip()
    if opinion not in _VALID_OPINIONS:
        logger.warning("LLM analyst returned invalid opinion: %r", parsed.get("opinion"))
        return None

    model_rec = str(payload.get("recommendation", "")).lower()
    model_bullish = "buy" in model_rec
    agrees = (opinion == "BUY") == model_bullish

    return {
        "opinion": opinion,
        "confidence": str(parsed.get("confidence", "")).lower() or "unknown",
        "rationale": str(parsed.get("rationale", "")).strip(),
        "key_risks": [str(r) for r in parsed.get("key_risks", []) if str(r).strip()],
        "agrees_with_model": agrees,
        "model": settings.llm_model,
        "disclaimer": (
            "AI-generated second opinion for a human to weigh — not advice, not an "
            "instruction to trade. The calibrated model score remains the signal of record."
        ),
    }


_EXPLAIN_SYSTEM_PROMPT = (
    "You explain WHY a stock model reached its verdict, to a COMPLETE BEGINNER, in warm, "
    "plain, everyday English. You are given a ticker, the model's recommendation, its "
    "conviction (0-100), and its top drivers (each: a name, a value, and whether it pushed "
    "the score UP or DOWN). The model predicts FORWARD returns — whether the stock has room "
    "to rise from here — NOT how it has done in the past.\n"
    "\n"
    "Your job is to translate the drivers into the INTUITION behind the decision, not to "
    "define terms. For each key driver, say what it IMPLIES for the investment case and why "
    "that pushes the score up or down — plain cause-and-effect. For example: a low earnings "
    "yield or high valuation means the stock is expensive relative to what it earns, so "
    "you'd be paying a lot and there's less room left to run; a strong long-term uptrend "
    "means it has momentum on its side; a weak margin means profitability isn't giving the "
    "model much to lean on.\n"
    "\n"
    "Then land ONE crisp bottom-line sentence that captures the trade-off the model is "
    "weighing — often a 'yes, but' tension (e.g. 'a strong, rising company, but you'd be "
    "buying it expensive, and expensive things have less room to run').\n"
    "\n"
    "Write 2-4 sentences of flowing prose (<=75 words total — no lists, no markdown, no "
    "preamble). You may add light, widely-known context about what the company does, but "
    "NEVER invent specific figures, prices, news, or events you aren't given. Be honest and "
    "balanced — never salesy — and NEVER give advice or tell the user to buy or sell."
)


def plain_explanation(payload: dict, settings: Settings) -> str | None:
    """Ask the LLM for a short, beginner-friendly narrative of the drivers.

    Reuses an already-computed score payload (ticker, recommendation, conviction,
    drivers) — no re-scoring — and returns plain prose, or ``None`` when disabled/failed.
    """
    if not settings.llm_analyst_enabled or not settings.openai_api_key:
        return None

    import requests

    drivers = [
        {
            "name": d.get("feature"),
            "value": round(float(d.get("value", 0.0)), 4)
            if d.get("value") is not None
            else None,
            "pushed": "up" if float(d.get("shap", 0.0)) >= 0 else "down",
        }
        for d in payload.get("drivers", [])
    ]
    evidence = {
        "ticker": payload.get("ticker"),
        "model_recommendation": payload.get("recommendation"),
        "model_conviction_0_100": payload.get("conviction"),
        "top_drivers": drivers,
        "news_tone": payload.get("news_label"),
    }

    body: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.45,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": _EXPLAIN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Explain in plain English:\n" + json.dumps(evidence, default=str),
            },
        ],
    }
    try:
        resp = requests.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        content = str(resp.json()["choices"][0]["message"]["content"]).strip()
    except Exception:  # noqa: BLE001 — the primary score must never depend on the LLM
        logger.exception("LLM plain-explanation call failed")
        return None

    return content or None
