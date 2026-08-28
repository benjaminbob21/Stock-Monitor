"""Provider-level tests for the legacy-CIK merge after issuer reorganizations.

SEC's ticker map re-pointed XOM at a freshly created CIK (2115436) whose
companyfacts only carries the two 10-Qs filed since the 2026 reorganization;
the 15-year annual history stayed on the predecessor CIK (34088). When a
mapped CIK is paired with a known legacy CIK, the provider must merge both
histories so PIT consumers (TTM, DCF) see the full record.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from stock_monitor.providers.edgar_provider import _LEGACY_CIKS, EdgarProvider


class FakeResp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def _payload(ocf_entries: list[dict]) -> dict:
    return {
        "facts": {
            "us-gaap": {
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf_entries}}
            }
        }
    }


def test_legacy_cik_facts_are_merged() -> None:
    assert _LEGACY_CIKS["XOM"] == 34088
    fresh = _payload(
        [{"end": "2026-06-30", "filed": "2026-08-03", "val": 32_260_000_000, "form": "10-Q"}]
    )
    legacy = _payload(
        [
            {"end": "2024-12-31", "filed": "2025-02-26", "val": 55_022_000_000, "form": "10-K"},
            {"end": "2025-12-31", "filed": "2026-02-18", "val": 51_970_000_000, "form": "10-K"},
        ]
    )
    responses = {"2115436": FakeResp(fresh), "34088": FakeResp(legacy)}
    with (
        patch.object(EdgarProvider, "_load_ticker_map", return_value={"XOM": 2115436}),
        patch.object(
            EdgarProvider,
            "_get",
            side_effect=lambda url: responses[
                url.rsplit("CIK", 1)[1].split(".")[0].lstrip("0") or "0"
            ],
        ),
    ):
        provider = EdgarProvider()
        facts = provider.get_fundamentals(
            "XOM", concepts=("NetCashProvidedByUsedInOperatingActivities",)
        )
    values = sorted(f.value for f in facts)
    assert values == [32_260_000_000.0, 51_970_000_000.0, 55_022_000_000.0]
    assert all(f.ticker == "XOM" for f in facts)
    assert any(f.form == "10-K" for f in facts)


def test_legacy_fetch_failure_is_not_fatal() -> None:
    fresh = _payload(
        [{"end": "2026-06-30", "filed": "2026-08-03", "val": 32_260_000_000, "form": "10-Q"}]
    )
    with (
        patch.object(EdgarProvider, "_load_ticker_map", return_value={"XOM": 2115436}),
        patch.object(
            EdgarProvider,
            "_get",
            side_effect=[
                FakeResp(fresh),
                FakeResp({}, status_code=404),
            ],
        ),
    ):
        provider = EdgarProvider()
        facts = provider.get_fundamentals(
            "XOM", concepts=("NetCashProvidedByUsedInOperatingActivities",)
        )
    assert [f.value for f in facts] == [32_260_000_000.0]


def test_same_cik_skips_legacy_fetch() -> None:
    payload = _payload(
        [{"end": "2024-12-31", "filed": "2025-02-26", "val": 55_022_000_000, "form": "10-K"}]
    )
    calls: list[str] = []

    def fake_get(url: str) -> FakeResp:
        calls.append(url)
        return FakeResp(payload)

    with (
        patch.object(EdgarProvider, "_load_ticker_map", return_value={"XOM": 34088}),
        patch.object(EdgarProvider, "_get", side_effect=fake_get),
    ):
        provider = EdgarProvider()
        facts = provider.get_fundamentals(
            "XOM", concepts=("NetCashProvidedByUsedInOperatingActivities",)
        )
    assert len(calls) == 1  # only the mapped CIK, no legacy fetch
    assert [f.value for f in facts] == [55_022_000_000.0]
    assert all(f.known_on <= date.today() for f in facts)
