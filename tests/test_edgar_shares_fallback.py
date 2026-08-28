"""Provider-level tests for the dei→diluted shares fallback.

Mastercard (and a few other issuers) stopped filing ``dei:
EntityCommonStockSharesOutstanding`` after 2010, leaving a 15-year-old share
count that inflated DCF per-share values ~7×. The provider must also emit the
us-gaap diluted weighted average whenever it is fresher than dei.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from stock_monitor.providers.edgar_provider import EdgarProvider


def _provider_with(payload: dict) -> list:
    class FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return payload

    with (
        patch.object(EdgarProvider, "_load_ticker_map", return_value={"MA": 1141391}),
        patch.object(EdgarProvider, "_get", return_value=FakeResp()),
    ):
        provider = EdgarProvider()
        return provider.get_fundamentals("MA", concepts=("CommonStockSharesOutstanding",))


def _shares_payload(entries: list[dict]) -> dict:
    return {
        "facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": entries}}}}
    }


def test_dei_only_returns_dei_facts() -> None:
    payload = _shares_payload(
        [{"end": "2010-10-27", "filed": "2010-11-02", "val": 122_530_193, "form": "10-Q"}]
    )
    facts = _provider_with(payload)
    assert len(facts) == 1
    assert facts[0].value == 122_530_193


def test_stale_dei_plus_fresh_diluted_emits_both() -> None:
    payload = _shares_payload(
        [{"end": "2010-10-27", "filed": "2010-11-02", "val": 122_530_193, "form": "10-Q"}]
    )
    payload["facts"]["us-gaap"] = {
        "WeightedAverageNumberOfDilutedSharesOutstanding": {
            "units": {
                "shares": [
                    {"end": "2026-06-30", "filed": "2026-07-30", "val": 888_000_000, "form": "10-Q"}
                ]
            }
        }
    }
    facts = _provider_with(payload)
    values = sorted(f.value for f in facts)
    assert values == [122_530_193, 888_000_000]
    freshest = max(facts, key=lambda f: f.known_on)
    assert freshest.known_on == date(2026, 7, 30)


def test_fresh_dei_not_shadowed_by_older_diluted() -> None:
    payload = _shares_payload(
        [{"end": "2026-07-17", "filed": "2026-07-20", "val": 14_700_000_000, "form": "10-Q"}]
    )
    payload["facts"]["us-gaap"] = {
        "WeightedAverageNumberOfDilutedSharesOutstanding": {
            "units": {
                "shares": [
                    {
                        "end": "2025-06-30",
                        "filed": "2025-07-30",
                        "val": 15_100_000_000,
                        "form": "10-K",
                    }
                ]
            }
        }
    }
    facts = _provider_with(payload)
    assert len(facts) == 1
    assert facts[0].known_on == date(2026, 7, 20)
