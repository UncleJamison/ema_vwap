"""
Paper Trading Profiles & Execution Engine Endpoints.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

import math
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth import get_api_key
from src.logger import logger

router = APIRouter(tags=["Paper Trading"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


class DeployPaperProfileRequest(BaseModel):
    exchange: str = "kucoin"
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    strategy_mode: str = "crossover"
    target_metric: str = "sharpe_ratio"
    params: dict[str, Any]
    optuna_score: float = 0.0
    is_active: bool = True
    validation_report: dict[str, Any] | None = None


@router.post("/api/paper/profiles/deploy", dependencies=[Depends(get_api_key)])
def deploy_paper_profile(req: DeployPaperProfileRequest) -> dict[str, Any]:
    """1-Click deployment: Save & activate an Optuna trial parameter set for a symbol."""
    try:
        profile_id = _st()._paper_profiles.save_profile(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            strategy_mode=req.strategy_mode,
            target_metric=req.target_metric,
            params=req.params,
            optuna_score=req.optuna_score,
            is_active=req.is_active,
            deployed=True,
            validation_report=req.validation_report,
        )
        saved = _st()._paper_profiles.get_profile(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            strategy_mode=req.strategy_mode,
        )
        logger.info(
            f"[API] Deployed Optuna parameters to active paper profile for {req.symbol} ({req.exchange}, {req.timeframe})"
        )
        return {
            "status": "success",
            "message": f"Successfully deployed Optuna parameters to paper profile for {req.symbol}.",
            "profile": {
                "id": profile_id,
                "exchange": req.exchange,
                "symbol": req.symbol,
                "timeframe": req.timeframe,
                "strategy_mode": req.strategy_mode,
                "target_metric": req.target_metric,
                "params": req.params,
                "optuna_score": req.optuna_score,
                "is_active": req.is_active,
                "updated_at": saved.updated_at if saved else None,
                "validation_report": req.validation_report,
            },
        }
    except Exception as e:
        logger.error(f"[API] deploy_paper_profile error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/paper/profiles")
def list_paper_profiles(
    exchange: str | None = None, active_only: bool = False
) -> dict[str, Any]:
    """List all saved per-asset paper trading profiles."""
    try:
        profiles = _st()._paper_profiles.list_profiles(
            exchange=exchange, active_only=active_only
        )
        return {
            "status": "success",
            "count": len(profiles),
            "profiles": [
                {
                    "id": p.profile_id,
                    "exchange": p.exchange,
                    "symbol": p.symbol,
                    "timeframe": p.timeframe,
                    "strategy_mode": p.strategy_mode,
                    "target_metric": p.target_metric,
                    "params": p.params,
                    "optuna_score": p.optuna_score,
                    "is_active": p.is_active,
                    "created_at": p.created_at,
                    "updated_at": p.updated_at,
                }
                for p in profiles
            ],
        }
    except Exception as e:
        logger.error(f"[API] list_paper_profiles error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/paper/profiles/{exchange}/{symbol:path}")
def get_paper_profile_for_symbol(
    exchange: str,
    symbol: str,
    timeframe: str | None = None,
    strategy_mode: str | None = None,
) -> dict[str, Any]:
    """Get the active paper profile for a specific exchange and symbol."""
    try:
        profile = _st()._paper_profiles.get_profile(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=strategy_mode,
        )
        if not profile:
            raise HTTPException(
                status_code=404,
                detail=f"No paper profile found for {symbol} on {exchange}.",
            )
        return {
            "status": "success",
            "profile": {
                "id": profile.profile_id,
                "exchange": profile.exchange,
                "symbol": profile.symbol,
                "timeframe": profile.timeframe,
                "strategy_mode": profile.strategy_mode,
                "target_metric": profile.target_metric,
                "params": profile.params,
                "optuna_score": profile.optuna_score,
                "is_active": profile.is_active,
                "created_at": profile.created_at,
                "updated_at": profile.updated_at,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] get_paper_profile_for_symbol error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class ToggleActiveRequest(BaseModel):
    is_active: bool


@router.post(
    "/api/paper/profiles/{profile_id}/toggle-active",
    dependencies=[Depends(get_api_key)],
)
def toggle_paper_profile_active(
    profile_id: int, req: ToggleActiveRequest
) -> dict[str, Any]:
    """Toggle the active state of a paper profile."""
    updated = _st()._paper_profiles.set_active(profile_id, req.is_active)
    if not updated:
        raise HTTPException(status_code=404, detail="Paper profile not found.")
    return {"status": "success", "id": profile_id, "is_active": req.is_active}


@router.delete("/api/paper/profiles/{profile_id}", dependencies=[Depends(get_api_key)])
def delete_paper_profile(profile_id: int) -> dict[str, Any]:
    """Delete a paper profile by ID."""
    deleted = _st()._paper_profiles.delete_profile(profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Paper profile not found.")
    return {"status": "success", "deleted": True, "id": profile_id}


class UpdateProfileTradeDirectionRequest(BaseModel):
    trade_direction: Literal["long_only", "long_short", "short_only"]


@router.patch(
    "/api/paper/profiles/{profile_id}/trade-direction",
    dependencies=[Depends(get_api_key)],
)
def update_profile_trade_direction(
    profile_id: int, req: UpdateProfileTradeDirectionRequest
) -> dict[str, Any]:
    """Update the trade direction / account mode for a deployed paper profile."""
    updated = _st()._paper_profiles.update_trade_direction(
        profile_id, req.trade_direction
    )
    if not updated:
        raise HTTPException(
            status_code=404, detail=f"Paper profile ID {profile_id} not found."
        )
    return {
        "status": "success",
        "profile_id": profile_id,
        "trade_direction": req.trade_direction,
        "message": f"Updated profile {profile_id} trade direction to {req.trade_direction}.",
    }


class PaperStepRequest(BaseModel):
    exchange: str | None = None
    symbol: str | None = None
    timeframe: str = "5m"


@router.post("/api/paper/engine/step", dependencies=[Depends(get_api_key)])
def step_paper_engine(req: PaperStepRequest) -> dict[str, Any]:
    """Execute a single evaluation step on a specific symbol or all active paper profiles."""
    try:
        if req.exchange and req.symbol:
            res = _st()._paper_engine.evaluate_symbol(
                exchange=req.exchange,
                symbol=req.symbol,
                timeframe=req.timeframe,
                fetch_live=True,
            )
            return {"status": "success", "results": [res]}
        else:
            results = _st()._paper_engine.step_all_active_profiles()
            return {"status": "success", "results": results}
    except Exception as e:
        logger.error(f"[API] step_paper_engine error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class PaperPollingRequest(BaseModel):
    interval_seconds: int = 15


@router.post("/api/paper/engine/start", dependencies=[Depends(get_api_key)])
def start_paper_polling(req: PaperPollingRequest) -> dict[str, Any]:
    """Start background live polling runner for paper trading."""
    started = _st()._paper_engine.start_polling(interval_seconds=req.interval_seconds)
    return {
        "status": "success" if started else "already_running",
        "is_running": _st()._paper_engine.is_running,
        "interval_seconds": req.interval_seconds,
    }


@router.post("/api/paper/engine/stop", dependencies=[Depends(get_api_key)])
def stop_paper_polling() -> dict[str, Any]:
    """Stop background live polling runner for paper trading."""
    stopped = _st()._paper_engine.stop_polling()
    return {
        "status": "success" if stopped else "not_running",
        "is_running": _st()._paper_engine.is_running,
    }


@router.post("/api/paper/engine/reconcile", dependencies=[Depends(get_api_key)])
def reconcile_paper_engine() -> dict[str, Any]:
    """Audit open positions against missed historical candles and backfill SL/TP exits."""
    try:
        results = _st()._paper_engine.reconcile_positions_on_startup()
        return {
            "status": "success",
            "reconciled_count": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error(f"[API] reconcile_paper_engine error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/paper/engine/status")
def get_paper_engine_status() -> dict[str, Any]:
    """Get live execution engine runner status, positions, balance, and stats."""
    return _st()._paper_engine.get_status()


@router.get("/api/paper/positions")
def list_paper_positions(exchange: str | None = None) -> dict[str, Any]:
    """List all currently active open paper trading positions."""
    try:
        positions = _st()._paper_ledger.list_positions(exchange=exchange)
        return {
            "status": "success",
            "count": len(positions),
            "positions": [
                {
                    "exchange": p.exchange,
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "quantity": p.quantity,
                    "cost_basis": p.cost_basis,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                    "entry_time": p.entry_time,
                    "updated_time": p.updated_time,
                    "metadata": p.metadata,
                }
                for p in positions
            ],
        }
    except Exception as e:
        logger.error(f"[API] list_paper_positions error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/api/paper/positions/{exchange}/{symbol:path}/close",
    dependencies=[Depends(get_api_key)],
)
def close_paper_position_manual(exchange: str, symbol: str) -> dict[str, Any]:
    """Manually close an open paper position at current market price."""
    try:
        trade = _st()._paper_engine.close_position_market(
            exchange=exchange, symbol=symbol
        )
        if not trade:
            raise HTTPException(
                status_code=404,
                detail=f"No open paper position found for {symbol} on {exchange}.",
            )
        return {
            "status": "success",
            "message": f"Successfully closed position for {symbol}.",
            "trade": {
                "trade_id": trade.trade_id,
                "exchange": trade.exchange,
                "symbol": trade.symbol,
                "side": trade.side,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "quantity": trade.quantity,
                "realized_pnl": trade.realized_pnl,
                "realized_pnl_pct": trade.realized_pnl_pct,
                "exit_reason": trade.exit_reason,
                "exit_time": trade.exit_time,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] close_paper_position_manual error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class UpdateSLTPRequest(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


@router.post(
    "/api/paper/positions/{exchange}/{symbol:path}/sl_tp",
    dependencies=[Depends(get_api_key)],
)
def update_paper_position_sl_tp(
    exchange: str, symbol: str, req: UpdateSLTPRequest
) -> dict[str, Any]:
    """Dynamically update Stop-Loss and/or Take-Profit levels on an active open paper position."""
    try:
        updated = _st()._paper_engine.update_position_sl_tp(
            exchange=exchange,
            symbol=symbol,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
        )
        return {
            "status": "success",
            "message": f"Successfully updated SL/TP for {symbol}.",
            "position": updated,
        }
    except ValueError as ve:
        logger.warning(f"[API] update_paper_position_sl_tp: {ve}")
        raise HTTPException(
            status_code=404,
            detail=f"No open paper position found for {symbol} on {exchange}.",
        )
    except Exception as e:
        logger.error(f"[API] update_paper_position_sl_tp error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/paper/ledger/transactions")
def list_paper_ledger_transactions(
    account_id: str = "default",
    exchange: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List double-entry paper ledger transaction audit logs."""
    try:
        txs = _st()._paper_ledger.list_transactions(
            account_id=account_id, exchange=exchange, symbol=symbol, limit=limit
        )
        return {
            "status": "success",
            "count": len(txs),
            "transactions": [
                {
                    "transaction_id": t.transaction_id,
                    "account_id": t.account_id,
                    "exchange": t.exchange,
                    "symbol": t.symbol,
                    "type": t.type,
                    "amount": t.amount,
                    "asset_qty": t.asset_qty,
                    "price": t.price,
                    "fee": t.fee,
                    "balance_after": t.balance_after,
                    "timestamp": t.timestamp,
                    "notes": t.notes,
                }
                for t in txs
            ],
        }
    except Exception as e:
        logger.error(f"[API] list_paper_ledger_transactions error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/paper/ledger/deposit", dependencies=[Depends(get_api_key)])
def deposit_paper_ledger(
    account_id: str = "default",
    amount: float = 10000.0,
    notes: str = "Deposit",
) -> dict[str, Any]:
    """Deposit cash into paper trading account."""
    try:
        if (
            not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount <= 0
        ):
            raise HTTPException(
                status_code=400, detail="Amount must be a finite positive number"
            )

        tx = _st()._paper_ledger.deposit(
            account_id=account_id, amount=amount, notes=notes
        )
        return {
            "status": "success",
            "transaction": {
                "transaction_id": tx.transaction_id,
                "account_id": tx.account_id,
                "type": tx.type,
                "amount": tx.amount,
                "balance_after": tx.balance_after,
                "timestamp": tx.timestamp,
                "notes": tx.notes,
            },
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[API] deposit_paper_ledger error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/paper/ledger/reset", dependencies=[Depends(get_api_key)])
def reset_paper_ledger(
    account_id: str = "default",
) -> dict[str, Any]:
    """Reset paper ledger to initial state ($10,000 balance, no positions, no history)."""
    try:
        result = _st()._paper_ledger.reset(account_id=account_id)
        return {
            "status": "success",
            "message": f"Paper ledger for account '{account_id}' reset to initial state",
            "deleted": result,
        }
    except Exception as e:
        logger.error(f"[API] reset_paper_ledger error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/paper/trades/history")
def list_paper_trade_history(
    exchange: str | None = None,
    symbol: str | None = None,
    exit_reason: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List completed round-trip paper trade history logs."""
    try:
        trades = _st()._paper_ledger.list_trade_history(
            exchange=exchange, symbol=symbol, exit_reason=exit_reason, limit=limit
        )
        return {
            "status": "success",
            "count": len(trades),
            "trades": [
                {
                    "trade_id": t.trade_id,
                    "exchange": t.exchange,
                    "symbol": t.symbol,
                    "timeframe": t.timeframe,
                    "side": t.side,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "holding_period_bars": t.holding_period_bars,
                    "realized_pnl": t.realized_pnl,
                    "realized_pnl_pct": t.realized_pnl_pct,
                    "fees": t.fees,
                    "exit_reason": t.exit_reason,
                }
                for t in trades
            ],
        }
    except Exception as e:
        logger.error(f"[API] list_paper_trade_history error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/paper/performance")
def get_paper_performance(
    exchange: str | None = None, symbol: str | None = None
) -> dict[str, Any]:
    """Get aggregate paper trading performance metrics and equity stats."""
    try:
        stats = _st()._paper_ledger.get_statistics(exchange=exchange, symbol=symbol)
        balance = _st()._paper_ledger.get_balance()
        positions = _st()._paper_ledger.list_positions(exchange=exchange)
        unrealized = sum(p.unrealized_pnl for p in positions)
        positions_market_value = sum(p.cost_basis + p.unrealized_pnl for p in positions)
        total_equity = balance + positions_market_value
        return {
            "status": "success",
            "cash_balance": round(balance, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(unrealized, 2),
            "open_positions": len(positions),
            "statistics": stats,
        }
    except Exception as e:
        logger.error(f"[API] get_paper_performance error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
