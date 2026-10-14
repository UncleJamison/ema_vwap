"""
Order Management System (OMS) Endpoints (ema_vwap-6a1).

Provides REST API for order lifecycle management, pre-trade risk checks,
TCA estimation, and execution algorithm selection.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.auth import get_api_key
from src.oms import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
    compute_implementation_shortfall,
    post_trade_tca,
    pre_trade_risk,
    pre_trade_tca,
)
from src.oms.lifecycle import order_manager

router = APIRouter(prefix="/api/oms", tags=["Order Management System"])


# =============================================================================
# Request/Response Models
# =============================================================================


class OrderCreateRequest(BaseModel):
    """Create a new order."""

    symbol: str = Field(..., description="Trading symbol (e.g., BTC/USD, AAPL)")
    side: str = Field(..., description="buy or sell")
    quantity: float = Field(..., gt=0, description="Order quantity")
    order_type: str = Field(
        default="market", description="market, limit, stop, stop_limit"
    )
    limit_price: float | None = Field(
        default=None, description="Limit price for limit orders"
    )
    stop_price: float | None = Field(
        default=None, description="Stop price for stop orders"
    )
    time_in_force: str = Field(default="gtc", description="gtc, ioc, fok, day")
    venue: str = Field(
        default="auto",
        description="Execution venue (auto, kucoin, gemini, alpaca, paper)",
    )
    account_id: str = Field(default="default")
    client_order_id: str | None = None
    algorithm: str | None = Field(default=None, description="TWAP, VWAP, POV, IS")
    algorithm_params: dict[str, Any] = Field(default_factory=dict)


class OrderResponse(BaseModel):
    """Order response."""

    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: float | None
    stop_price: float | None
    time_in_force: str
    venue: str
    account_id: str
    status: str
    created_at: float
    updated_at: float
    filled_quantity: float
    avg_fill_price: float
    remaining_quantity: float
    commission_usd: float
    exchange_fees_usd: float
    error_message: str | None


class OrderCancelRequest(BaseModel):
    """Cancel order request."""

    reason: str = "User requested"


class OrderSubmitRequest(BaseModel):
    """Submit order to venue."""

    arrival_price: float | None = None


class FillRequest(BaseModel):
    """Record a fill."""

    fill_qty: float = Field(..., gt=0)
    fill_price: float = Field(..., gt=0)
    fee_usd: float = 0.0
    exchange_fee_usd: float = 0.0
    fill_id: str | None = None


class PreTradeRiskRequest(BaseModel):
    """Pre-trade risk check request."""

    symbol: str
    side: str
    quantity: float
    price: float
    portfolio_nav: float
    current_positions: dict[str, float] = {}
    adv: float = 1_000_000
    sector: str = "unknown"


class PreTradeTCARequest(BaseModel):
    """Pre-trade TCA request."""

    symbol: str
    side: str
    quantity: float
    arrival_price: float
    market_context: dict | None = None
    order_type: str = "market"
    urgency: str = "normal"
    algorithm: str = "IS"


class RiskConfigUpdateRequest(BaseModel):
    """Update pre-trade risk config."""

    max_order_size_pct_nav: float | None = None
    max_participation_pct_adv: float | None = None
    max_position_pct_nav: float | None = None
    max_asset_concentration_pct: float | None = None
    max_daily_loss_pct: float | None = None
    max_orders_per_minute: int | None = None
    min_order_size_usd: float | None = None
    max_leverage: float | None = None


# =============================================================================
# Helper
# =============================================================================


def _order_to_response(order: Order) -> OrderResponse:
    return OrderResponse(
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        side=order.side.value,
        order_type=order.order_type.value,
        quantity=order.quantity,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        time_in_force=order.time_in_force.value,
        venue=order.venue,
        account_id=order.account_id,
        status=order.status.value,
        created_at=order.created_at,
        updated_at=order.updated_at,
        filled_quantity=order.filled_quantity,
        avg_fill_price=order.avg_fill_price,
        remaining_quantity=order.remaining_quantity,
        commission_usd=order.commission_usd,
        exchange_fees_usd=order.exchange_fees_usd,
        error_message=order.error_message,
    )


# =============================================================================
# Order CRUD
# =============================================================================


@router.post("/orders", dependencies=[Depends(get_api_key)])
async def create_order(req: OrderCreateRequest) -> OrderResponse:
    """Create a new order in NEW state."""
    try:
        side = OrderSide(req.side.lower())
        order_type = OrderType(req.order_type.lower())
        tif = TimeInForce(req.time_in_force.lower())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid enum value: {e}")

    order = order_manager.create_order(
        symbol=req.symbol,
        side=side,
        quantity=req.quantity,
        order_type=order_type,
        limit_price=req.limit_price,
        stop_price=req.stop_price,
        time_in_force=tif,
        venue=req.venue,
        account_id=req.account_id,
        client_order_id=req.client_order_id,
        algorithm=req.algorithm,
        algorithm_params=req.algorithm_params,
    )
    return _order_to_response(order)


@router.get("/orders")
async def list_orders(
    status: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List orders with optional filters."""
    orders = list(order_manager._orders.values())

    if status:
        try:
            status_enum = OrderStatus(status.lower())
            orders = [o for o in orders if o.status == status_enum]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    if symbol:
        orders = [o for o in orders if o.symbol.upper() == symbol.upper()]

    # Sort by created_at desc
    orders.sort(key=lambda o: o.created_at, reverse=True)

    total = len(orders)
    paginated = orders[offset : offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "orders": [_order_to_response(o) for o in paginated],
    }


@router.get("/orders/{order_id}")
async def get_order(order_id: str) -> OrderResponse:
    """Get a single order by ID."""
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return _order_to_response(order)


@router.post("/orders/{order_id}/submit", dependencies=[Depends(get_api_key)])
async def submit_order(order_id: str, req: OrderSubmitRequest) -> OrderResponse:
    """Submit order to venue (NEW -> PENDING)."""
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.NEW:
        raise HTTPException(
            status_code=400, detail=f"Cannot submit order in {order.status.value} state"
        )

    order_manager.submit_order(order_id, req.arrival_price)
    return _order_to_response(order)


@router.post("/orders/{order_id}/fill", dependencies=[Depends(get_api_key)])
async def record_fill(order_id: str, req: FillRequest) -> OrderResponse:
    """Record a fill (partial or full)."""
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.is_active():
        raise HTTPException(
            status_code=400, detail=f"Order not active: {order.status.value}"
        )

    order_manager.process_fill(
        order_id,
        req.fill_qty,
        req.fill_price,
        req.fee_usd,
        req.exchange_fee_usd,
        fill_id=req.fill_id,
    )
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return _order_to_response(order)


@router.post("/orders/{order_id}/cancel", dependencies=[Depends(get_api_key)])
async def cancel_order(order_id: str, req: OrderCancelRequest) -> OrderResponse:
    """Request order cancellation."""
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order_manager.cancel_order(order_id, req.reason)
    return _order_to_response(order)


@router.post("/orders/{order_id}/confirm_cancel", dependencies=[Depends(get_api_key)])
async def confirm_cancel(order_id: str) -> OrderResponse:
    """Confirm cancellation from venue."""
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order_manager.confirm_cancel(order_id)
    return _order_to_response(order)


# =============================================================================
# Pre-Trade Risk Checks
# =============================================================================


@router.post("/risk/check", dependencies=[Depends(get_api_key)])
async def check_pre_trade_risk(req: PreTradeRiskRequest) -> dict[str, Any]:
    """Run pre-trade risk checks for an order."""
    try:
        side = req.side.lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid side")

    passed, results = pre_trade_risk.check_order(
        symbol=req.symbol,
        side=side,
        quantity=req.quantity,
        price=req.price,
        portfolio_nav=req.portfolio_nav,
        current_positions=req.current_positions,
        adv=req.adv,
        sector=req.sector,
    )

    return {
        "passed": passed,
        "checks": [
            {
                "check": r.check_name,
                "passed": r.passed,
                "current": r.current_value,
                "threshold": r.threshold,
                "message": r.message,
                "action": r.action,
            }
            for r in results
        ],
    }


@router.get("/risk/config")
async def get_risk_config() -> dict[str, Any]:
    """Get current pre-trade risk configuration."""
    return {
        "max_order_size_pct_nav": pre_trade_risk.config.max_order_size_pct_nav,
        "max_participation_pct_adv": pre_trade_risk.config.max_participation_pct_adv,
        "max_position_pct_nav": pre_trade_risk.config.max_position_pct_nav,
        "max_asset_concentration_pct": pre_trade_risk.config.max_asset_concentration_pct,
        "max_sector_concentration_pct": pre_trade_risk.config.max_sector_concentration_pct,
        "max_daily_loss_pct": pre_trade_risk.config.max_daily_loss_pct,
        "max_orders_per_minute": pre_trade_risk.config.max_orders_per_minute,
        "max_orders_per_day": pre_trade_risk.config.max_orders_per_day,
        "min_order_size_usd": pre_trade_risk.config.min_order_size_usd,
        "max_leverage": pre_trade_risk.config.max_leverage,
        "checks_enabled": pre_trade_risk.config.checks_enabled,
    }


@router.patch("/risk/config", dependencies=[Depends(get_api_key)])
async def update_risk_config(req: RiskConfigUpdateRequest) -> dict[str, Any]:
    """Update pre-trade risk configuration."""
    config = pre_trade_risk.config
    if req.max_order_size_pct_nav is not None:
        config.max_order_size_pct_nav = req.max_order_size_pct_nav
    if req.max_participation_pct_adv is not None:
        config.max_participation_pct_adv = req.max_participation_pct_adv
    if req.max_position_pct_nav is not None:
        config.max_position_pct_nav = req.max_position_pct_nav
    if req.max_asset_concentration_pct is not None:
        config.max_asset_concentration_pct = req.max_asset_concentration_pct
    if req.max_daily_loss_pct is not None:
        config.max_daily_loss_pct = req.max_daily_loss_pct
    if req.max_orders_per_minute is not None:
        config.max_orders_per_minute = req.max_orders_per_minute
    if req.min_order_size_usd is not None:
        config.min_order_size_usd = req.min_order_size_usd
    if req.max_leverage is not None:
        config.max_leverage = req.max_leverage

    return {
        "message": "Risk configuration updated",
        "config": {
            "max_order_size_pct_nav": config.max_order_size_pct_nav,
            "max_participation_pct_adv": config.max_participation_pct_adv,
            "max_position_pct_nav": config.max_position_pct_nav,
            "max_asset_concentration_pct": config.max_asset_concentration_pct,
            "max_daily_loss_pct": config.max_daily_loss_pct,
            "max_orders_per_minute": config.max_orders_per_minute,
            "min_order_size_usd": config.min_order_size_usd,
            "max_leverage": config.max_leverage,
        },
    }


# =============================================================================
# TCA Endpoints
# =============================================================================


@router.post("/tca/pre_trade", dependencies=[Depends(get_api_key)])
async def pre_trade_tca_endpoint(req: PreTradeTCARequest) -> dict[str, Any]:
    """Estimate execution costs before trading (pre-trade TCA)."""
    try:
        side = req.side.lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid side")

    result = pre_trade_tca(
        symbol=req.symbol,
        quantity=req.quantity,
        side=side,
        arrival_price=req.arrival_price,
        market_context=req.market_context,
        order_type=req.order_type,
        urgency=req.urgency,
        algorithm=req.algorithm,
    )
    return result


@router.post("/tca/post_trade", dependencies=[Depends(get_api_key)])
async def post_trade_tca_endpoint(order_id: str) -> dict[str, Any]:
    """Calculate post-trade TCA for a filled order."""
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    result = post_trade_tca(order)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/tca/implementation_shortfall", dependencies=[Depends(get_api_key)])
async def implementation_shortfall(
    symbol: str,
    quantity: float,
    side: str,
    arrival_price: float,
    avg_fill_price: float,
    commission_usd: float = 0.0,
    exchange_fees_usd: float = 0.0,
) -> dict[str, float]:
    """Calculate implementation shortfall in basis points."""
    is_bps = compute_implementation_shortfall(
        symbol,
        quantity,
        side.lower(),
        arrival_price,
        avg_fill_price,
        commission_usd,
        exchange_fees_usd,
    )
    return {"implementation_shortfall_bps": round(is_bps, 2)}


# =============================================================================
# Algorithm Selection
# =============================================================================


@router.get("/algorithms")
async def list_algorithms() -> dict[str, Any]:
    """List available execution algorithms."""
    return {
        "algorithms": [
            {
                "id": "TWAP",
                "name": "Time-Weighted Average Price",
                "params": ["windows"],
            },
            {"id": "VWAP", "name": "Volume-Weighted Average Price", "params": []},
            {"id": "POV", "name": "Percentage of Volume", "params": ["pov_pct"]},
            {
                "id": "IS",
                "name": "Implementation Shortfall",
                "params": ["risk_aversion"],
            },
        ]
    }


# =============================================================================
# Risk Configuration
# =============================================================================


@router.get("/orders/active")
async def get_active_orders() -> list[OrderResponse]:
    """Get all active (non-terminal) orders."""
    orders = order_manager.get_active_orders()
    return [_order_to_response(o) for o in orders]


@router.post("/risk/daily_pnl", dependencies=[Depends(get_api_key)])
async def update_daily_pnl(pnl_change: float) -> dict[str, Any]:
    """Update daily P&L for risk calculations."""
    from src.oms.risk_checks import pre_trade_risk

    pre_trade_risk.update_daily_pnl(pnl_change)
    return {"message": "Daily P&L updated", "daily_pnl": pre_trade_risk._daily_pnl}


@router.post("/risk/starting_nav", dependencies=[Depends(get_api_key)])
async def set_starting_nav(nav: float) -> dict[str, Any]:
    """Set starting NAV for daily loss limit calculation."""
    from src.oms.risk_checks import pre_trade_risk

    pre_trade_risk.set_starting_nav(nav)
    return {"message": "Starting NAV set", "starting_nav": nav}
