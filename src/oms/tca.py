"""
Transaction Cost Analysis — Almgren-Chriss Market Impact & Slippage Model (ema_vwap-6a1).

Implements the Almgren-Chriss framework for optimal execution and slippage estimation.
Used for pre-trade TCA and post-trade attribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass
class MarketParameters:
    """Market microstructure parameters for Almgren-Chriss model."""

    # Daily volatility (annualized, e.g., 0.25 = 25%)
    daily_volatility: float = 0.25

    # Average daily volume (shares/contracts)
    adv: float = 1_000_000

    # Bid-ask spread in basis points
    spread_bps: float = 5.0

    # Temporary impact coefficient (how much price moves per unit of volume)
    # Typical: 0.1 - 1.0 for liquid stocks, higher for crypto
    temporary_impact_coeff: float = 0.1

    # Permanent impact coefficient (information leakage)
    permanent_impact_coeff: float = 0.1

    # Risk aversion parameter (lambda) - higher = more urgency to finish
    risk_aversion: float = 1e-6

    # Trading horizon in days
    horizon_days: float = 1.0


@dataclass
class ExecutionPlan:
    """Optimal execution trajectory from Almgren-Chriss."""

    # Number of time slices
    n_slices: int

    # Quantity per slice
    slice_quantities: list[float]

    # Expected temporary impact per slice (bps)
    temp_impact_bps: list[float]

    # Expected permanent impact per slice (bps)
    perm_impact_bps: list[float]

    # Total expected cost (bps)
    total_cost_bps: float

    # Implementation shortfall (bps)
    implementation_shortfall_bps: float


def estimate_adv_from_context(symbol: str, market_context: dict | None = None) -> float:
    """Estimate ADV from symbol and optional context."""
    # Default estimates by asset class
    defaults = {
        "BTC": 50_000,  # BTC daily volume ~50k BTC
        "ETH": 500_000,  # ETH daily volume ~500k ETH
        "SOL": 5_000_000,  # SOL daily volume ~5M SOL
        "AAPL": 50_000_000,  # AAPL daily volume ~50M shares
        "SPY": 80_000_000,  # SPY daily volume ~80M shares
    }

    base = symbol.split("/")[0].split("-")[0].upper()
    return (
        market_context.get("adv", defaults.get(base, 1_000_000))
        if market_context
        else defaults.get(base, 1_000_000)
    )


def estimate_spread_bps(symbol: str, market_context: dict | None = None) -> float:
    """Estimate bid-ask spread in basis points."""
    defaults = {
        "BTC": 2.0,
        "ETH": 3.0,
        "SOL": 5.0,
        "AAPL": 1.0,
        "SPY": 0.5,
    }
    base = symbol.split("/")[0].split("-")[0].upper()
    return (
        market_context.get("spread_bps", defaults.get(base, 10.0))
        if market_context
        else defaults.get(base, 10.0)
    )


def estimate_volatility(symbol: str, market_context: dict | None = None) -> float:
    """Estimate daily volatility (annualized)."""
    defaults = {
        "BTC": 0.45,
        "ETH": 0.55,
        "SOL": 0.80,
        "AAPL": 0.25,
        "SPY": 0.15,
    }
    base = symbol.split("/")[0].split("-")[0].upper()
    return (
        market_context.get("volatility", defaults.get(base, 0.30))
        if market_context
        else defaults.get(base, 0.30)
    )


def almgren_chriss_optimal_trajectory(
    total_quantity: float,
    market_params: MarketParameters,
    n_slices: int = 10,
    urgency: str = "normal",  # "low", "normal", "high"
) -> ExecutionPlan:
    """
    Compute optimal execution trajectory using Almgren-Chriss model.

    The model balances:
    - Temporary market impact (convex, scales with trade rate)
    - Permanent market impact (linear, information leakage)
    - Risk (price uncertainty during execution)

    Optimal trajectory is front-loaded for high urgency, back-loaded for low.
    """
    # Adjust risk aversion based on urgency
    lambda_param = market_params.risk_aversion
    if urgency == "high":
        lambda_param *= 10
    elif urgency == "low":
        lambda_param *= 0.1

    # Time horizon in trading days (convert to years for annualized vol)
    T = market_params.horizon_days / 252.0
    _sigma = market_params.daily_volatility / math.sqrt(252.0)  # Daily vol

    # Almgren-Chriss parameters
    # eta = temporary impact coefficient
    eta = market_params.temporary_impact_coeff
    # gamma = permanent impact coefficient
    gamma = market_params.permanent_impact_coeff

    # Characteristic time scale: tau = sqrt(eta * sigma^2 / (lambda * gamma))
    # But simpler formulation: kappa = sqrt(lambda * gamma / eta)
    kappa = math.sqrt(lambda_param * gamma / eta) if eta > 0 else 1.0

    # Generate slice quantities
    slice_quantities = []
    temp_impact = []
    perm_impact = []

    remaining = total_quantity
    for i in range(n_slices):
        t = (i + 0.5) / n_slices * T

        # Optimal trading rate: x(t) = X * (kappa * cosh(kappa * (T-t)) / sinh(kappa * T))
        if kappa * T > 0.01:
            rate = math.cosh(kappa * (T - t)) / math.sinh(kappa * T)
        else:
            rate = 1.0 / T  # Linear limit

        slice_qty = total_quantity * rate * (T / n_slices)
        slice_qty = min(slice_qty, remaining)
        remaining -= slice_qty

        if slice_qty <= 0:
            slice_quantities.append(0.0)
            temp_impact.append(0.0)
            perm_impact.append(0.0)
            continue

        slice_quantities.append(slice_qty)

        # Participation rate for this slice
        participation = slice_qty / market_params.adv if market_params.adv > 0 else 0.0

        # Temporary impact: eta * (trade_rate / ADV)
        trade_rate = slice_qty / (T / n_slices)  # shares per day
        temp_imp = eta * (trade_rate / market_params.adv) * 10000  # bps
        temp_impact.append(max(0.0, temp_imp))

        # Permanent impact: gamma * (trade_rate / ADV)
        perm_imp = gamma * participation * 10000  # bps
        perm_impact.append(max(0.0, perm_imp))

    total_temp = sum(temp_impact)
    total_perm = sum(perm_impact)
    spread_cost = market_params.spread_bps / 2  # Half-spread per round trip

    total_cost = total_temp + total_perm + spread_cost
    implementation_shortfall = total_cost  # Simplified

    return ExecutionPlan(
        n_slices=n_slices,
        slice_quantities=slice_quantities,
        temp_impact_bps=temp_impact,
        perm_impact_bps=perm_impact,
        total_cost_bps=total_cost,
        implementation_shortfall_bps=implementation_shortfall,
    )


def estimate_slippage_bps(
    symbol: str,
    quantity: float,
    side: str,  # "buy" or "sell"
    market_context: dict | None = None,
    order_type: str = "market",
    urgency: str = "normal",
) -> tuple[float, float, float]:
    """
    Estimate slippage in basis points for a single order.

    Returns: (slippage_bps, market_impact_bps, total_cost_bps)
    """
    adv = estimate_adv_from_context(symbol, market_context)
    spread = estimate_spread_bps(symbol, market_context)
    vol = estimate_volatility(symbol, market_context)

    participation = quantity / adv if adv > 0 else 0.0

    # Market impact (Almgren-Chriss simplified)
    # Impact ~ spread/2 + temp_impact + perm_impact
    temp_impact_coeff = 0.1 * (1 + vol * 5)  # Higher vol = more impact
    perm_impact_coeff = 0.1 * (1 + vol * 3)

    trade_rate = quantity  # per slice
    temp_impact = temp_impact_coeff * (trade_rate / max(adv, 1)) * 10000
    perm_impact = perm_impact_coeff * participation * 10000

    # Spread cost (half-spread for market orders)
    spread_cost = spread / 2 if order_type == "market" else 0.0

    # Urgency multiplier
    urgency_mult = {"low": 0.5, "normal": 1.0, "high": 2.0}.get(urgency, 1.0)
    temp_impact *= urgency_mult
    perm_impact *= urgency_mult

    market_impact = temp_impact + perm_impact
    slippage = spread_cost + market_impact

    # Side adjustment (buy pays more, sell receives less)
    # This is captured in the sign of the cost
    if side.lower() == "sell":
        # Sell: we receive less, so negative slippage from mid
        pass

    return slippage, market_impact, spread_cost + market_impact


def compute_implementation_shortfall(
    symbol: str,
    quantity: float,
    side: str,
    arrival_price: float,
    avg_fill_price: float,
    commission_usd: float = 0.0,
    exchange_fees_usd: float = 0.0,
) -> float:
    """
    Compute implementation shortfall in basis points.

    IS = (avg_fill_price - arrival_price) / arrival_price * 10000  (for BUY)
    IS = (arrival_price - avg_fill_price) / arrival_price * 10000  (for SELL)
    """
    if arrival_price <= 0:
        return 0.0

    if side.lower() == "buy":
        shortfall = (avg_fill_price - arrival_price) / arrival_price * 10000
    else:
        shortfall = (arrival_price - avg_fill_price) / arrival_price * 10000

    # Add commission/fees
    total_cost = (
        (commission_usd + exchange_fees_usd) / (arrival_price * quantity) * 10000
    )

    return shortfall + total_cost


def pre_trade_tca(
    symbol: str,
    quantity: float,
    side: str,
    arrival_price: float,
    market_context: dict | None = None,
    order_type: str = "market",
    urgency: str = "normal",
    algorithm: str = "IS",
) -> dict[str, Any]:
    """
    Pre-trade TCA: estimate all costs before execution.

    Returns dict with slippage, market impact, spread, commission, total cost, IS estimate.
    """
    slippage_bps, impact_bps, spread_bps = estimate_slippage_bps(
        symbol, quantity, side, market_context, order_type, urgency
    )

    # Commission estimate (typical: 1-5 bps)
    commission_bps = 2.0 if "crypto" in symbol.lower() or "/" in symbol else 1.0

    # Exchange fees
    exchange_fee_bps = 1.0 if "crypto" in symbol.lower() or "/" in symbol else 0.5

    total_cost_bps = slippage_bps + commission_bps + exchange_fee_bps

    # Estimated fill price
    if side.lower() == "buy":
        est_fill = arrival_price * (1 + total_cost_bps / 10000)
    else:
        est_fill = arrival_price * (1 - total_cost_bps / 10000)

    # IS estimate
    is_bps = compute_implementation_shortfall(
        symbol, quantity, side, arrival_price, est_fill
    )

    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "arrival_price": arrival_price,
        "estimated_fill_price": round(est_fill, 6),
        "slippage_bps": round(slippage_bps, 2),
        "market_impact_bps": round(impact_bps, 2),
        "spread_bps": round(spread_bps, 2),
        "commission_bps": round(commission_bps, 2),
        "exchange_fee_bps": round(exchange_fee_bps, 2),
        "total_estimated_cost_bps": round(total_cost_bps, 2),
        "implementation_shortfall_bps": round(is_bps, 2),
        "algorithm": algorithm,
        "urgency": urgency,
    }


def post_trade_tca(
    order,
    market_context: dict | None = None,
) -> dict[str, Any]:
    """
    Post-trade TCA: attribute costs after execution.

    Requires order with fills, arrival_price, avg_fill_price.
    """
    if not order.fills:
        return {"error": "No fills recorded"}

    if order.arrival_price is None:
        return {"error": "Arrival price not recorded"}

    is_bps = compute_implementation_shortfall(
        order.symbol,
        order.quantity,
        order.side.value,
        order.arrival_price,
        order.avg_fill_price,
        order.commission_usd,
        order.exchange_fees_usd,
    )

    # Decompose
    slippage_bps = 0.0
    for fill in order.fills:
        if order.side == "buy":
            fill_slip = (
                (fill["price"] - order.arrival_price) / order.arrival_price * 10000
            )
        else:
            fill_slip = (
                (order.arrival_price - fill["price"]) / order.arrival_price * 10000
            )
        slippage_bps += fill_slip * (fill["quantity"] / order.quantity)

    commission_bps = (
        order.commission_usd / (order.arrival_price * order.quantity) * 10000
        if order.arrival_price > 0
        else 0
    )
    exchange_fee_bps = (
        order.exchange_fees_usd / (order.arrival_price * order.quantity) * 10000
        if order.arrival_price > 0
        else 0
    )

    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": order.quantity,
        "arrival_price": order.arrival_price,
        "avg_fill_price": order.avg_fill_price,
        "slippage_bps": round(slippage_bps, 2),
        "commission_bps": round(commission_bps, 2),
        "exchange_fee_bps": round(exchange_fee_bps, 2),
        "implementation_shortfall_bps": round(is_bps, 2),
        "fills": len(order.fills),
        "total_filled": order.filled_quantity,
    }
