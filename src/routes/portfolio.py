"""
Unified Multi-Asset Portfolio Management Endpoints.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.app_limiter import limiter
from src.auth import get_api_key
from src.logger import logger
from src.portfolio import portfolio_aggregator

router = APIRouter(tags=["Portfolio"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


@router.get("/api/portfolio/snapshot")
def get_portfolio_snapshot(
    include_synthetic: bool = False,
) -> dict[str, Any]:
    """Retrieve consolidated multi-asset portfolio snapshot across all connected venues."""
    try:
        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=include_synthetic
        )
        return {
            "status": "success",
            "snapshot": snapshot.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] get_portfolio_snapshot error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/portfolio/venues")
def get_portfolio_venues(
    include_synthetic: bool = False,
) -> dict[str, Any]:
    """Retrieve connectivity, balances, and NAV per individual venue."""
    try:
        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=include_synthetic
        )
        return {
            "status": "success",
            "timestamp": snapshot.timestamp,
            "total_nav_usd": round(snapshot.total_nav_usd, 2),
            "venues": {k: v.to_dict() for k, v in snapshot.venues.items()},
        }
    except Exception as e:
        logger.error(f"[API] get_portfolio_venues error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/portfolio/positions")
def get_portfolio_positions(
    include_synthetic: bool = False,
) -> dict[str, Any]:
    """Retrieve aggregated cross-asset position holdings."""
    try:
        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=include_synthetic
        )
        return {
            "status": "success",
            "timestamp": snapshot.timestamp,
            "total_positions": len(snapshot.positions),
            "total_market_value_usd": round(
                sum(p.market_value_usd for p in snapshot.positions), 2
            ),
            "positions": [p.to_dict() for p in snapshot.positions],
        }
    except Exception as e:
        logger.error(f"[API] get_portfolio_positions error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/portfolio/refresh", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
def refresh_portfolio_balances(
    request: Request,
    include_synthetic: bool = False,
) -> dict[str, Any]:
    """Force real-time balance refresh across all connected venues."""
    try:
        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=include_synthetic
        )
        return {
            "status": "success",
            "message": "Portfolio balances refreshed successfully.",
            "snapshot": snapshot.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] refresh_portfolio_balances error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/portfolio/history")
def get_portfolio_historical_nav(
    days: int = 30, include_synthetic: bool = True
) -> dict[str, Any]:
    """Retrieve historical daily portfolio aggregate NAV trajectory."""
    try:
        history = portfolio_aggregator.get_historical_nav(
            days=days, include_synthetic=include_synthetic
        )
        return {
            "status": "success",
            "days": days,
            "data_points": len(history),
            "history": history,
        }
    except Exception as e:
        logger.error(f"[API] get_portfolio_historical_nav error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class PositionSizingRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_loss_price: float
    risk_per_trade_pct: float = 1.0
    allow_fractional: bool = True
    include_synthetic: bool = False


@router.get("/api/portfolio/correlation")
def get_portfolio_correlation(
    symbols: str | None = None,
) -> dict[str, Any]:
    """Retrieve cross-asset correlation matrix and diversification score."""
    try:
        from src.portfolio.correlation import CrossAssetCorrelationEngine

        sym_list = [s.strip() for s in symbols.split(",")] if symbols else None
        returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
            symbols=sym_list
        )
        metrics = CrossAssetCorrelationEngine.compute_correlation_matrix(returns_df)
        return {
            "status": "success",
            "metrics": metrics,
        }
    except Exception as e:
        logger.error(f"[API] get_portfolio_correlation error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/portfolio/var")
def get_portfolio_var(
    confidence_level: float = 0.95,
    horizon_days: int = 1,
    include_synthetic: bool = False,
) -> dict[str, Any]:
    """Retrieve portfolio Value at Risk, CVaR, Component VaR, and Drawdown Circuit Breakers."""
    try:
        from src.portfolio.risk_engine import PortfolioRiskEngine

        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=include_synthetic
        )
        risk_report = PortfolioRiskEngine.compute_portfolio_var(
            snapshot=snapshot,
            confidence_level=confidence_level,
            horizon_days=horizon_days,
        )
        return {
            "status": "success",
            "risk_report": risk_report,
        }
    except Exception as e:
        logger.error(f"[API] get_portfolio_var error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/portfolio/size_position", dependencies=[Depends(get_api_key)])
@limiter.limit("30/minute")
def size_portfolio_position(
    request: Request,
    req: PositionSizingRequest,
) -> dict[str, Any]:
    """Calculate correlation-penalized and risk-budgeted position sizing for a prospective trade."""
    try:
        from src.portfolio.risk_budget import CrossAssetRiskBudgeter

        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=req.include_synthetic
        )
        sizing = CrossAssetRiskBudgeter.calculate_position_size(
            symbol=req.symbol,
            entry_price=req.entry_price,
            stop_loss_price=req.stop_loss_price,
            snapshot=snapshot,
            risk_per_trade_pct=req.risk_per_trade_pct,
            allow_fractional=req.allow_fractional,
        )
        return {
            "status": "success",
            "sizing": sizing,
        }
    except Exception as e:
        logger.error(f"[API] size_portfolio_position error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class RouteOrderRequest(BaseModel):
    symbol: str
    side: int  # 1 for BUY, -1 for SELL
    quantity: float
    venue: str | None = "auto"
    order_type: str = "market"
    limit_price: float | None = None
    dry_run: bool = False


class RebalancePlanRequest(BaseModel):
    target_weights: dict[str, float]  # e.g. {"BTC/USD": 0.3, "AAPL": 0.3, "SPY": 0.4}
    drift_threshold_pct: float = 2.0
    min_trade_usd: float = 25.0
    include_synthetic: bool = False


class RebalanceExecuteRequest(BaseModel):
    target_weights: dict[str, float]
    drift_threshold_pct: float = 2.0
    min_trade_usd: float = 25.0
    dry_run: bool = True
    include_synthetic: bool = False


@router.post("/api/portfolio/route_order", dependencies=[Depends(get_api_key)])
def route_portfolio_order(req: RouteOrderRequest) -> dict[str, Any]:
    """Route a discrete buy or sell order to appropriate exchange, broker, or paper ledger."""
    try:
        from src.portfolio.order_router import order_router

        order = order_router.route_order(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            venue=req.venue,
            order_type=req.order_type,
            limit_price=req.limit_price,
            dry_run=req.dry_run,
        )
        return {
            "status": "success",
            "order": order.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] route_portfolio_order error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/portfolio/rebalance/plan", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
def create_portfolio_rebalance_plan(
    request: Request, req: RebalancePlanRequest
) -> dict[str, Any]:
    """Generate a multi-asset portfolio rebalancing plan."""
    try:
        from src.portfolio.rebalancer import PortfolioRebalancingEngine

        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=req.include_synthetic
        )
        plan = PortfolioRebalancingEngine.create_rebalance_plan(
            target_weights=req.target_weights,
            snapshot=snapshot,
            drift_threshold_pct=req.drift_threshold_pct,
            min_trade_usd=req.min_trade_usd,
        )
        return {
            "status": "success",
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] create_portfolio_rebalance_plan error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/portfolio/rebalance/execute", dependencies=[Depends(get_api_key)])
def execute_portfolio_rebalance(req: RebalanceExecuteRequest) -> dict[str, Any]:
    """Execute a multi-asset portfolio rebalancing plan across target venues."""
    try:
        from src.portfolio.rebalancer import PortfolioRebalancingEngine

        snapshot = portfolio_aggregator.get_unified_snapshot(
            include_synthetic=req.include_synthetic
        )
        plan = PortfolioRebalancingEngine.create_rebalance_plan(
            target_weights=req.target_weights,
            snapshot=snapshot,
            drift_threshold_pct=req.drift_threshold_pct,
            min_trade_usd=req.min_trade_usd,
        )
        orders = PortfolioRebalancingEngine.execute_plan(plan, dry_run=req.dry_run)
        return {
            "status": "success",
            "dry_run": req.dry_run,
            "executed_orders_count": len(orders),
            "orders": [o.to_dict() for o in orders],
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] execute_portfolio_rebalance error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class PortfolioRiskBudgetRequest(BaseModel):
    """Request for portfolio-level risk budgeting computation."""

    symbols: list[str]
    # Optional per-asset metrics (if None, estimated from synthetic returns)
    asset_metrics: dict[str, dict[str, float]] | None = None
    # Risk parity weighting (True) vs inverse-volatility (False)
    risk_parity: bool = True
    # Per-asset max drawdown budget (0.0 = no per-asset constraint)
    max_drawdown_per_asset_pct: float = 35.0
    # Max single-asset weight cap
    max_single_asset_weight: float = 0.30
    # Min single-asset weight floor
    min_single_asset_weight: float = 0.0
    # MC simulation parameters
    mc_simulations: int = 500
    mc_horizon_days: int = 252
    initial_capital: float = 10000.0
    risk_free_rate: float = 0.0


@router.post("/api/portfolio/risk_budget", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
def compute_portfolio_risk_budget(
    request: Request,
    req: PortfolioRiskBudgetRequest,
) -> dict[str, Any]:
    """
    Compute portfolio-level risk budgeting with risk parity weights,
    per-asset max drawdown constraints, and portfolio-level Monte Carlo
    stress testing using correlated multivariate simulation.
    """
    try:
        from src.portfolio.portfolio_risk_budget import (
            PortfolioRiskBudgetEngine,
            RiskBudgetConfig,
        )

        config = RiskBudgetConfig(
            risk_parity=req.risk_parity,
            max_drawdown_per_asset_pct=req.max_drawdown_per_asset_pct,
            max_single_asset_weight=req.max_single_asset_weight,
            min_single_asset_weight=req.min_single_asset_weight,
            mc_simulations=req.mc_simulations,
            risk_free_rate=req.risk_free_rate,
        )

        result = PortfolioRiskBudgetEngine.compute_risk_budget(
            symbols=req.symbols,
            asset_metrics=req.asset_metrics,
            config=config,
        )

        return {
            "status": "success",
            "result": result.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] compute_portfolio_risk_budget error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
