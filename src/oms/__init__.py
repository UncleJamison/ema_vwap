"""
Order Management System (OMS) — ema_vwap-6a1.

Modules:
- lifecycle: Order state machine (NEW -> PENDING -> PARTIALLY_FILLED -> FILLED/CANCELLED/REJECTED/EXPIRED)
- tca: Transaction Cost Analysis (Almgren-Chriss slippage & market impact model)
- risk_checks: Pre-trade risk gates (size, participation, concentration, daily loss, rate limits)
"""

from .lifecycle import (
    Order,
    OrderEvent,
    OrderManager,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    order_manager,
)
from .risk_checks import (
    PreTradeRiskConfig,
    PreTradeRiskManager,
    RiskCheckResult,
    pre_trade_risk,
)
from .tca import (
    ExecutionPlan,
    MarketParameters,
    almgren_chriss_optimal_trajectory,
    compute_implementation_shortfall,
    estimate_slippage_bps,
    post_trade_tca,
    pre_trade_tca,
)

__all__ = [
    "ExecutionPlan",
    "MarketParameters",
    "Order",
    "OrderEvent",
    "OrderManager",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PreTradeRiskConfig",
    "PreTradeRiskManager",
    "RiskCheckResult",
    "TimeInForce",
    "almgren_chriss_optimal_trajectory",
    "compute_implementation_shortfall",
    "estimate_slippage_bps",
    "order_manager",
    "post_trade_tca",
    "pre_trade_risk",
    "pre_trade_tca",
]
