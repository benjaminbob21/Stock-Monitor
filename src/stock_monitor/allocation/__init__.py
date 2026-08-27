"""Capital allocation — deterministic, risk-aware portfolio sizing.

Approved design (2026-08-27): the ENGINE computes weights from auditable inputs
(conviction / volatility, sentiment haircuts, risk-flag penalties, caps and floors).
LLMs never produce numbers here; they only narrate results downstream.
"""

from stock_monitor.allocation.contracts import (
    AllocationConstraints,
    AllocationPlan,
    PortfolioState,
    PositionAllocation,
    PositionInput,
)
from stock_monitor.allocation.engine import allocate

__all__ = [
    "AllocationConstraints",
    "AllocationPlan",
    "PositionAllocation",
    "PositionInput",
    "PortfolioState",
    "allocate",
]
