"""SEC EDGAR fundamental provider — the point-in-time source of truth.

EDGAR's ``companyfacts`` API returns structured XBRL financials where **every value
carries the date it was filed** (``filed``). That filing date is our ``known_on``
guarantee against look-ahead bias, which is why EDGAR — not a convenience API that
serves restated figures — is the fundamentals backbone (build-plan §1, §3).

SEC fair-access policy requires a descriptive ``User-Agent`` with contact info; set
it via ``SEC_USER_AGENT`` in ``.env``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import requests
import requests_cache
from tenacity import retry, stop_after_attempt, wait_exponential

from stock_monitor.config import get_settings
from stock_monitor.providers.base import FundamentalFact, FundamentalProvider

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# A small, robust starter set mapped to quality/value factors (build-plan §2).
DEFAULT_CONCEPTS: tuple[str, ...] = (
    "NetIncomeLoss",
    # Quarterly net income is often filed under this alias only (e.g. Mastercard
    # reports 10-Q income under ProfitLoss, leaving NetIncomeLoss to DEF 14A annuals).
    "ProfitLoss",
    "StockholdersEquity",
    "Assets",
    "Liabilities",
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    # Valuation inputs (market cap + free cash flow).
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CommonStockSharesOutstanding",
)

# Shares outstanding is also commonly filed under the dei taxonomy; we normalise it
# to the us-gaap concept name so the feature builder can look it up uniformly.
_DEI_SHARES_CONCEPT = "EntityCommonStockSharesOutstanding"

# Some issuers stopped populating dei shares years ago (Mastercard last filed it
# for FY2010). Their cover-page share counts only exist as us-gaap weighted
# averages; diluted is the conservative per-share denominator, so it is emitted
# as a fallback alias of CommonStockSharesOutstanding.
_DILUTED_SHARES_CONCEPT = "WeightedAverageNumberOfDilutedSharesOutstanding"

# Cash/valuation concepts the DCF engine needs beyond DEFAULT_CONCEPTS.
_DCF_EXTRA_CONCEPTS: tuple[str, ...] = (
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "ShortTermBorrowings",
    # Capex aliases: some issuers (BLBD, V, AIG) file the broader
    # PaymentsToAcquireProductiveAssets instead of the PP&E tag, and a few
    # (SPGI, PLTR) split capex across several PaymentsToAcquire* tags.
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    "PaymentsForCapitalExpenditures",
    "PaymentsToAcquireOtherProductiveAssets",
    "PaymentsToAcquireInterestInJointVenture",
)

# SEC's ticker map occasionally re-points a ticker at a freshly created CIK
# (e.g. XOM -> CIK 2115436 after a 2026 reorganization) whose companyfacts
# holds only the filings since the change. The full filing history stays on
# the predecessor CIK, so when the mapped CIK looks too thin we merge in the
# legacy CIK's facts. PIT consumers then pick facts by known_on as usual.
_LEGACY_CIKS: dict[str, int] = {"XOM": 34088}


def dcf_concepts() -> tuple[str, ...]:
    """All us-gaap concepts the DCF engine reads (defaults + cash-flow extras)."""
    return tuple(dict.fromkeys([*DEFAULT_CONCEPTS, *_DCF_EXTRA_CONCEPTS]))


def _freshest_filed(payload: dict) -> str:
    """Latest ``filed`` date across a companyfacts concept payload (ISO strings)."""
    return max(
        (
            entry.get("filed", "")
            for entries in payload.get("units", {}).values()
            for entry in entries
        ),
        default="",
    )


class EdgarProvider(FundamentalProvider):
    """Fetch point-in-time fundamentals from SEC EDGAR ``companyfacts``."""

    name = "sec-edgar"

    def __init__(self) -> None:
        settings = get_settings()
        # Cached session respects SEC's servers and free-tier politeness.
        self._session = requests_cache.CachedSession(
            cache_name=".cache/edgar",
            backend="sqlite",
            expire_after=settings.http_cache_ttl,
        )
        self._session.headers.update({"User-Agent": settings.sec_user_agent})
        self._ticker_to_cik: dict[str, int] | None = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        reraise=True,
    )
    def _get(self, url: str) -> requests.Response:
        # Retries transient network errors (not HTTP 4xx/5xx, which callers handle).
        return self._session.get(url, timeout=30)

    def _load_ticker_map(self) -> dict[str, int]:
        if self._ticker_to_cik is None:
            resp = self._get(_TICKERS_URL)
            resp.raise_for_status()
            self._ticker_to_cik = {
                row["ticker"].upper(): int(row["cik_str"]) for row in resp.json().values()
            }
        return self._ticker_to_cik

    def _cik_for(self, ticker: str) -> int | None:
        return self._load_ticker_map().get(ticker.upper())

    def get_fundamentals(
        self, ticker: str, concepts: Sequence[str] | None = None
    ) -> list[FundamentalFact]:
        concepts = concepts or DEFAULT_CONCEPTS
        cik = self._cik_for(ticker)
        if cik is None:
            return []

        resp = self._get(_COMPANYFACTS_URL.format(cik=cik))
        if resp.status_code == 404:
            return []
        resp.raise_for_status()

        all_facts = resp.json().get("facts", {})
        us_gaap = all_facts.get("us-gaap", {})
        wanted = set(concepts)
        facts: list[FundamentalFact] = []

        def _emit(concept: str, payload: dict) -> None:
            for unit, entries in payload.get("units", {}).items():
                for entry in entries:
                    end = entry.get("end")
                    filed = entry.get("filed")
                    value = entry.get("val")
                    if end is None or filed is None or value is None:
                        continue
                    start_raw = entry.get("start")
                    facts.append(
                        FundamentalFact(
                            ticker=ticker.upper(),
                            concept=concept,
                            value=float(value),
                            unit=unit,
                            fiscal_end=date.fromisoformat(end),
                            known_on=date.fromisoformat(filed),
                            form=entry.get("form", ""),
                            period_start=(date.fromisoformat(start_raw) if start_raw else None),
                        )
                    )

        for concept, payload in us_gaap.items():
            if concept in wanted:
                _emit(concept, payload)

        # Shares outstanding is often only in the dei taxonomy; normalise its name.
        # dei cover-page counts stopped being filed by some issuers (Mastercard's
        # last one is FY2010), so whenever the freshest dei fact is older than the
        # freshest us-gaap diluted weighted average, emit that too — downstream
        # PIT-aware consumers pick whichever fact is freshest as of their date.
        if "CommonStockSharesOutstanding" in wanted:
            dei = all_facts.get("dei", {})
            dei_payload = dei.get(_DEI_SHARES_CONCEPT)
            if dei_payload is not None:
                _emit("CommonStockSharesOutstanding", dei_payload)
            diluted = us_gaap.get(_DILUTED_SHARES_CONCEPT)
            if diluted is not None and (
                dei_payload is None or _freshest_filed(diluted) > _freshest_filed(dei_payload)
            ):
                _emit("CommonStockSharesOutstanding", diluted)

        # A reorganized issuer's fresh CIK only carries post-reorganization
        # filings; merge the predecessor CIK's history so TTM/DCF consumers
        # still see the long annual record (facts dedupe by known_on+end+value).
        legacy_cik = _LEGACY_CIKS.get(ticker.upper())
        if legacy_cik is not None and legacy_cik != cik:
            legacy_resp = self._get(_COMPANYFACTS_URL.format(cik=legacy_cik))
            if legacy_resp.status_code == 200:
                legacy_resp.raise_for_status()
                legacy_gaap = legacy_resp.json().get("facts", {}).get("us-gaap", {})
                for concept, payload in legacy_gaap.items():
                    if concept in wanted:
                        _emit(concept, payload)
                if "CommonStockSharesOutstanding" in wanted:
                    legacy_dei = (
                        legacy_resp.json().get("facts", {}).get("dei", {}).get(_DEI_SHARES_CONCEPT)
                    )
                    if legacy_dei is not None:
                        _emit("CommonStockSharesOutstanding", legacy_dei)

        return facts
