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


def _json_dumps(obj: dict) -> str:
    return json.dumps(obj)
