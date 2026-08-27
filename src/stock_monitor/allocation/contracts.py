"""Allocation contracts — the shared dataclasses for the capital-allocation epic.

Design (approved 2026-08-27): a *deterministic engine* produces portfolio weights
from auditable inputs (model conviction, volatility, sentiment, risk flags). The LLM
never produces numbers; it only narrates. These dataclasses are the boundary between
the collectors (providers → alt-sentiment), the engine (weights math), and the API/UI.

All inputs are snapshots with an ``as_of`` timestamp so the engine can refuse to work
with stale data (freshness checks live in the engine, not here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PositionInput:
    """One candidate/invested position, as the engine sees it.

    ``conviction`` is the model's calibrated 0-100 score of record. ``volatility``
    is annualized (e.g. 0.35 = 35%). Sentiment terms are in [-1, 1]; alt_* are
    optional because collectors may not have covered the name yet.
    """

    ticker: str
    conviction: float
    volatility: float
    news_sentiment: float | None = None
    alt_sentiment: float | None = None
    risk_flags: tuple[str, ...] = ()

    @property
    def risk_penalty(self) -> float:
        """Multiplicative haircut per flag; engine applies it, contract defines it."""
        return 1.0 - min(0.10 * len(self.risk_flags), 0.30)


@dataclass(frozen=True)
class PortfolioState:
    """Where the money currently sits, for target-vs-current deltas."""

    total_value: float
    positions: tuple[tuple[str, float], ...]  # (ticker, current weight 0-1)


@dataclass(frozen=True)
class AllocationConstraints:
    """Guardrails the engine must respect. Values are fractions of total capital."""

    max_per_position: float = 0.15
    min_per_position: float = 0.02
    max_positions: int = 10
    cash_floor: float = 0.10  # kept in cash when aggregate conviction is weak


@dataclass(frozen=True)
class PositionAllocation:
    """The engine's verdict for one ticker — always with its reasons."""

    ticker: str
    target_weight: float
    current_weight: float
    conviction: float
    reasons: tuple[str, ...] = field(default=())

    @property
    def delta_weight(self) -> float:
        return self.target_weight - self.current_weight


@dataclass(frozen=True)
class AllocationPlan:
    """Full output of one engine run."""

    as_of: datetime
    total_value: float
    allocations: tuple[PositionAllocation, ...]
    cash_weight: float
    constraints: AllocationConstraints
    warnings: tuple[str, ...] = field(default=())
