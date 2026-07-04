"""Provider interface — the seam that lets data sources swap without touching the engine.

Two abstract roles (a source may implement one or both):
- `PriceProvider`   -> OHLCV history (e.g. yfinance).
- `FundamentalProvider` -> point-in-time fundamentals with a **known-on** date.

The `known_on` date on every `FundamentalFact` is the single most important field
in this project: it is the anti-look-ahead-bias guarantee. A fundamental value may
describe a fiscal period that ended months earlier, but it was only *knowable* to
the market on the day it was filed. Training or scoring on a value before its
`known_on` date is look-ahead bias — the #1 way ML stock models lie.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

# Canonical OHLCV column names every PriceProvider must return.
PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class FundamentalFact:
    """A single point-in-time fundamental datum.

    Attributes:
        ticker: Symbol the fact belongs to.
        concept: XBRL/us-gaap concept name (e.g. ``NetIncomeLoss``).
        value: Reported numeric value.
        unit: Unit of measure (e.g. ``USD``).
        fiscal_end: End date of the fiscal period the value describes.
        known_on: Date the fact became public (SEC filing date). PIT guarantee.
        form: Filing form type (e.g. ``10-K``, ``10-Q``).
    """

    ticker: str
    concept: str
    value: float
    unit: str
    fiscal_end: dt.date
    known_on: dt.date
    form: str


class PriceProvider(ABC):
    """Source of OHLCV price history."""

    name: str

    @abstractmethod
    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """Return a DataFrame indexed by date with :data:`PRICE_COLUMNS`.

        Prices should be split/dividend adjusted so returns are comparable over time.
        """
        raise NotImplementedError


class FundamentalProvider(ABC):
    """Source of point-in-time fundamentals."""

    name: str

    @abstractmethod
    def get_fundamentals(
        self, ticker: str, concepts: Sequence[str] | None = None
    ) -> list[FundamentalFact]:
        """Return every known-dated fact for ``ticker`` across ``concepts``."""
        raise NotImplementedError
