"""Execution algorithms for order routing (ema_vwap-e6f).

Standalone module: TWAP, VWAP, POV, Implementation Shortfall.
Includes slippage modeling, market impact estimation, adaptive execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExecutionOrder:
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    target_price: float | None = None
    urgency: str = "normal"  # "low", "normal", "high"


@dataclass
class FillResult:
    executed_qty: float
    avg_price: float
    slippage_pct: float
    market_impact_pct: float
    algorithm: str


class BaseExecutionAlgorithm:
    """Base for all smart-order-routing algorithms."""
    name: str = "base"

    def execute(self, order: ExecutionOrder, market_context: dict[str, Any] | None = None) -> FillResult:
        market_context = market_context or {}
        # Default deterministic fill at target with zero impact/slippage
        return FillResult(
            executed_qty=order.quantity,
            avg_price=order.target_price or 100.0,
            slippage_pct=0.0,
            market_impact_pct=0.0,
            algorithm=self.name,
        )


class TWAPAlgorithm(BaseExecutionAlgorithm):
    """Time-Weighted Average Price — slice large order across time windows."""
    name = "TWAP"

    def __init__(self, windows: int = 4) -> None:
        self.windows = windows

    def execute(self, order: ExecutionOrder, market_context: dict[str, Any] | None = None) -> FillResult:
        market_context = market_context or {}
        base = order.target_price or 100.0
        vol = market_context.get("volatility", 0.02)
        # More windows = more slices = more market footprint = higher impact
        impact = 0.05 * vol * (self.windows / 4.0)
        slippage = 0.03 * vol
        # Side-aware: BUY pays more, SELL receives less
        if order.side == "SELL":
            avg = base * (1 - impact - slippage)
        else:
            avg = base * (1 + impact + slippage)
        return FillResult(
            executed_qty=order.quantity,
            avg_price=avg,
            slippage_pct=slippage * 100,
            market_impact_pct=impact * 100,
            algorithm=self.name,
        )


class VWAPAlgorithm(BaseExecutionAlgorithm):
    """Volume-Weighted Average Price — slice against volume profile."""
    name = "VWAP"

    def execute(self, order: ExecutionOrder, market_context: dict[str, Any] | None = None) -> FillResult:
        market_context = market_context or {}
        base = order.target_price or 100.0
        vol_r = market_context.get("volume_ratio", 1.0)
        # VWAP should move price toward the volume profile, reducing impact with high volume
        if vol_r >= 5.0:  # High volume: tight to profile
            impact = 0.0015 * (1.0 / vol_r)
            slippage = 0.005 * (1.0 / vol_r)
        else:  # Normal volume: moderate impact
            impact = 0.04 * (1.0 / max(vol_r, 0.1))
            slippage = 0.02 * (1.0 / max(vol_r, 0.1))
        # Apply directionality based on order side
        if order.side == "SELL":
            avg = base * (1 - impact - slippage)
        else:
            avg = base * (1 + impact + slippage)
        return FillResult(
            executed_qty=order.quantity,
            avg_price=avg,
            slippage_pct=slippage * 100,
            market_impact_pct=impact * 100,
            algorithm=self.name,
        )


class POVAlgorithm(BaseExecutionAlgorithm):
    """Percentage of Volume (POV) — trade at fixed % of observed volume."""
    name = "POV"

    def __init__(self, pov_pct: float = 10.0) -> None:
        self.pov_pct = pov_pct

    def execute(self, order: ExecutionOrder, market_context: dict[str, Any] | None = None) -> FillResult:
        market_context = market_context or {}
        base = order.target_price or 100.0
        vol = market_context.get("volatility", 0.02)
        # Higher POV = more market footprint = higher impact (Almgren-Chriss style)
        impact = 0.06 * (self.pov_pct / 10.0) * (1 + vol * 5)
        slippage = 0.03 * (self.pov_pct / 10.0) * (1 + vol * 5)
        # POV fills proportional to participation rate (higher POV = more filled), capped at order qty
        fill_fraction = min(1.0, self.pov_pct / 100.0)
        executed = order.quantity * fill_fraction
        # Side-aware
        if order.side == "SELL":
            avg = base * (1 - impact - slippage)
        else:
            avg = base * (1 + impact + slippage)
        return FillResult(
            executed_qty=executed,
            avg_price=avg,
            slippage_pct=slippage * 100,
            market_impact_pct=impact * 100,
            algorithm=self.name,
        )


class ImplementationShortfallAlgorithm(BaseExecutionAlgorithm):
    """Implementation Shortfall (IS) — minimizes cost vs arrival price."""
    name = "IS"

    def __init__(self, risk_aversion: float = 1.0) -> None:
        self.risk_aversion = risk_aversion

    def execute(self, order: ExecutionOrder, market_context: dict[str, Any] | None = None) -> FillResult:
        market_context = market_context or {}
        base = order.target_price or 100.0
        vol = market_context.get("volatility", 0.02)
        # IS: impact scales with risk aversion and volatility; alpha can beat arrival
        impact = 0.02 * self.risk_aversion * (1 + vol * 5)
        slippage = 0.01 * self.risk_aversion * (1 + vol * 5)
        # Alpha: slight improvement for low risk aversion
        alpha = 0.005 * (1 - min(self.risk_aversion, 1.0))
        # Side-aware
        if order.side == "SELL":
            avg = base * (1 - impact - slippage + alpha)
        else:
            avg = base * (1 + impact + slippage - alpha)
        return FillResult(
            executed_qty=order.quantity,
            avg_price=avg,
            slippage_pct=slippage * 100,
            market_impact_pct=impact * 100,
            algorithm=self.name,
        )


ALGORITHMS = {
    "TWAP": TWAPAlgorithm,
    "VWAP": VWAPAlgorithm,
    "POV": POVAlgorithm,
    "IS": ImplementationShortfallAlgorithm,
}