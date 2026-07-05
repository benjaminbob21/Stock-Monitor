"""LLM analyst second-opinion tests (network-free via a fake HTTP call)."""

from __future__ import annotations

import json

from stock_monitor.analyst import second_opinion
from stock_monitor.config import Settings


def _payload() -> dict:
    return {
        "ticker": "AAA",
        "conviction": 82,
        "recommendation": "consider buying",
        "drivers": [
            {"feature": "mom_12_1", "direction": "+", "shap": 1.2, "value": 0.3},
            {"feature": "debt_ratio", "direction": "+", "shap": 0.8, "value": 0.2},
        ],
        "risk_flags": [],
        "news_sentiment": -0.1,
        "news_label": "neutral",
    }


def test_second_opinion_disabled_returns_none() -> None:
    assert second_opinion(_payload(), Settings(llm_analyst_enabled=False)) is None
    # Enabled but no key is also a no-op (never costs anything by accident).
    assert second_opinion(
        _payload(), Settings(llm_analyst_enabled=True, openai_api_key="")
    ) is None


class _FakeResp:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def test_second_opinion_parses_llm_json(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["auth"] = headers["Authorization"]
        captured["body"] = json
        content = _json_dumps(
            {
                "opinion": "buy",
                "confidence": "medium",
                "rationale": "Momentum solid, no risk flags.",
                "key_risks": ["valuation", "market drawdown"],
            }
        )
        return _FakeResp(content)

    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    settings = Settings(
        llm_analyst_enabled=True, openai_api_key="sk-test",
        llm_model="gpt-4o-mini", llm_base_url="https://api.openai.com/v1",
    )
    result = second_opinion(_payload(), settings)
    assert result is not None
    assert result["opinion"] == "BUY"
    assert result["confidence"] == "medium"
    assert result["agrees_with_model"] is True  # model said "consider buying"
    assert "valuation" in result["key_risks"]
    assert result["model"] == "gpt-4o-mini"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-test"


def test_second_opinion_rejects_invalid_opinion(monkeypatch) -> None:
    import requests

    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _FakeResp(_json_dumps({"opinion": "MAYBE"})),
    )
    settings = Settings(llm_analyst_enabled=True, openai_api_key="sk-test")
    assert second_opinion(_payload(), settings) is None


def test_second_opinion_includes_history_evidence(monkeypatch) -> None:
    """When present, historical analogs + news trend are passed to the LLM."""
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["body"] = json
        return _FakeResp(_json_dumps({"opinion": "HOLD", "confidence": "low"}))

    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    payload = _payload()
    payload["similar"] = {
        "base_rate": 0.6,
        "overall_base_rate": 0.5,
        "n_history": 120,
        "analogs": [
            {"ticker": "BBB", "as_of": "2020-01-02", "beat_benchmark": True},
        ],
    }
    payload["news_trend"] = {
        "direction": "improving",
        "recent_90d_mean": 0.2,
        "prior_90d_mean": 0.05,
        "latest": 0.3,
    }
    settings = Settings(llm_analyst_enabled=True, openai_api_key="sk-test")
    result = second_opinion(payload, settings)
    assert result is not None
    user_msg = captured["body"]["messages"][1]["content"]
    assert "similar_setups" in user_msg
    assert "base_rate_beat_benchmark" in user_msg
    assert "news_trend" in user_msg
    assert "improving" in user_msg


def test_blank_env_placeholder_uses_default(monkeypatch) -> None:
    """Blank `.env` placeholders (e.g. LLM_ANALYST_ENABLED=) fall back to defaults."""
    monkeypatch.setenv("LLM_ANALYST_ENABLED", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.llm_analyst_enabled is False  # blank bool -> default, not a crash
    assert settings.openai_api_key == ""  # blank str stays the disabled sentinel


def _json_dumps(obj: dict) -> str:
    return json.dumps(obj)
