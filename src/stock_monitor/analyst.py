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

    body = {
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
