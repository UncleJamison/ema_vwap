"""
Tear Sheet Export Endpoints.
Extracted from src/app.py as part of the modularization refactor (ema_vwap-tq41).
The endpoint bodies are moved verbatim; shared state is read through
`src.app` at call time so tests that monkeypatch `src.app._*` keep working.
"""

import inspect
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from src.app_limiter import limiter
from src.auth import get_api_key
from src.backtester import BacktestEngine
from src.config import StrategyParams, get_optimal_lookback_days
from src.data_loader import DataLoader
from src.logger import logger
from src.monte_carlo import MonteCarloConfig, MonteCarloSimulator
from src.validator import StrategyValidator

router = APIRouter(tags=["Tear Sheet"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


# -------------------------------------------------------------------------
# Tear Sheet Export Endpoints
# -------------------------------------------------------------------------


class TearSheetRequest(BaseModel):
    """Request to generate a tear sheet for a validated strategy."""

    exchange: str
    symbol: str
    timeframe: str
    params: dict[str, Any]
    # Optional: use cached validation report from a previous validation run
    validation_report: dict[str, Any] | None = None
    # Optional: override default Monte Carlo simulations for fresh run
    mc_simulations: int = 500
    target_metric: str = "sharpe_ratio"
    n_trials: int = 250
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] | None = None


@router.post("/api/tear-sheet/pdf", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
async def generate_tear_sheet_pdf(request: Request, req: TearSheetRequest) -> Response:
    """Generate and return a PDF tear sheet for the given strategy configuration."""
    try:
        from src.web.pdf_renderer import (
            generate_tear_sheet_pdf,
        )

        # Load candles for backtest and validation
        # "Auto (Optimal Lookback)" mode: use optimal lookback based on timeframe
        ts_days = get_optimal_lookback_days(req.timeframe)
        # Use async variant to avoid run_async() from main event loop
        sym_res = DataLoader.aresolve_canonical_symbol(req.symbol, live_mode=False)
        resolved_exchange, resolved_symbol = (
            await sym_res if inspect.isawaitable(sym_res) else sym_res
        )
        # Use async load_candles directly to avoid run_async() from main event loop
        candles_res = DataLoader.load_candles(
            exchange=resolved_exchange,
            symbol=resolved_symbol,
            timeframe=req.timeframe,
            limit=None,
            days=ts_days,
        )
        df_candles = (
            await candles_res if inspect.isawaitable(candles_res) else candles_res
        )

        # If no validation report provided, run validation
        if req.validation_report is None:
            strat_params = StrategyParams.from_dict(req.params)
            validator = StrategyValidator()
            report = validator.validate(
                df_candles=df_candles,
                params=strat_params,
                n_trials=req.n_trials,
                target_metric=req.target_metric,
                enable_multi_objective=req.enable_multi_objective,
                multi_objective_metrics=req.multi_objective_metrics,
                timeframe=req.timeframe,
            )
            validation_report = report.to_dict()
        else:
            validation_report = req.validation_report
            strat_params = StrategyParams.from_dict(req.params)

        # Run Monte Carlo for equity curves and percentiles

        engine = BacktestEngine(strat_params)
        bt_res = engine.run(df_candles)

        mc_cfg = MonteCarloConfig(
            num_simulations=req.mc_simulations, sample_with_replacement=True
        )
        mc_sim = MonteCarloSimulator(
            initial_capital=strat_params.initial_capital or 10000.0,
            trades=bt_res.trades,
            config=mc_cfg,
        )
        mc_report = mc_sim.run()

        # Build trade distribution
        pnls = [t.get("pnl", 0.0) for t in bt_res.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        trade_distribution = {
            "total_trades": len(pnls),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": (round(len(wins) / len(pnls) * 100, 1) if pnls else 0),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
            "profit_factor": (
                round(sum(wins) / abs(sum(losses)), 2) if losses else float("inf")
            ),
        }

        # Build equity curves for template
        equity_curves = mc_report.get(
            "equity_curves_percentiles", {"p5": [], "p50": [], "p95": []}
        )

        # Build Monte Carlo percentiles
        mc_percentiles = {
            "final_equity": mc_report.get("final_equity_percentiles", {}),
            "net_profit": mc_report.get("net_profit_percentiles", {}),
            "max_drawdown": mc_report.get("max_drawdown_percentiles", {}),
            "sharpe_ratio": mc_report.get("sharpe_ratio_percentiles", {}),
            "risk_of_ruin_pct": mc_report.get("risk_of_ruin_pct", 0),
            "simulations_count": mc_report.get("num_simulations", 0),
        }

        # Build context and generate PDF
        pdf_bytes = generate_tear_sheet_pdf(
            report=validation_report,
            strategy_params=req.params,
            symbol=req.symbol,
            timeframe=req.timeframe,
            exchange=req.exchange,
            trade_distribution=trade_distribution,
            equity_curves=equity_curves,
            monte_carlo_percentiles=mc_percentiles,
        )

        filename = f"ema_vwap_tear_sheet_{req.symbol.replace('/', '_')}_{req.timeframe}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}...."
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        logger.error(f"[API] generate_tear_sheet_pdf error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/tear-sheet/html", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
async def generate_tear_sheet_html(request: Request, req: TearSheetRequest) -> Response:
    """Generate and return an interactive HTML tear sheet for the given strategy configuration."""
    try:
        from src.web.pdf_renderer import (
            generate_tear_sheet_html,
        )

        # Load candles for backtest and validation
        # "Auto (Optimal Lookback)" mode: use optimal lookback based on timeframe
        ts_days = get_optimal_lookback_days(req.timeframe)
        # Use async variant to avoid run_async() from main event loop
        sym_res = DataLoader.aresolve_canonical_symbol(req.symbol, live_mode=False)
        resolved_exchange, resolved_symbol = (
            await sym_res if inspect.isawaitable(sym_res) else sym_res
        )
        # Use async load_candles directly to avoid run_async() from main event loop
        candles_res = DataLoader.load_candles(
            exchange=resolved_exchange,
            symbol=resolved_symbol,
            timeframe=req.timeframe,
            limit=None,
            days=ts_days,
        )
        df_candles = (
            await candles_res if inspect.isawaitable(candles_res) else candles_res
        )

        # If no validation report provided, run validation
        if req.validation_report is None:
            strat_params = StrategyParams.from_dict(req.params)
            validator = StrategyValidator()
            report = validator.validate(
                df_candles=df_candles,
                params=strat_params,
                n_trials=req.n_trials,
                target_metric=req.target_metric,
                enable_multi_objective=req.enable_multi_objective,
                multi_objective_metrics=req.multi_objective_metrics,
                timeframe=req.timeframe,
            )
            validation_report = report.to_dict()
        else:
            validation_report = req.validation_report
            strat_params = StrategyParams.from_dict(req.params)

        # Run Monte Carlo for equity curves and percentiles

        engine = BacktestEngine(strat_params)
        bt_res = engine.run(df_candles)

        mc_cfg = MonteCarloConfig(
            num_simulations=req.mc_simulations, sample_with_replacement=True
        )
        mc_sim = MonteCarloSimulator(
            initial_capital=strat_params.initial_capital or 10000.0,
            trades=bt_res.trades,
            config=mc_cfg,
        )
        mc_report = mc_sim.run()

        # Build trade distribution
        pnls = [t.get("pnl", 0.0) for t in bt_res.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        trade_distribution = {
            "total_trades": len(pnls),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": (round(len(wins) / len(pnls) * 100, 1) if pnls else 0),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
            "profit_factor": (
                round(sum(wins) / abs(sum(losses)), 2) if losses else float("inf")
            ),
        }

        # Build equity curves for template
        equity_curves = mc_report.get(
            "equity_curves_percentiles", {"p5": [], "p50": [], "p95": []}
        )

        # Build Monte Carlo percentiles
        mc_percentiles = {
            "final_equity": mc_report.get("final_equity_percentiles", {}),
            "net_profit": mc_report.get("net_profit_percentiles", {}),
            "max_drawdown": mc_report.get("max_drawdown_percentiles", {}),
            "sharpe_ratio": mc_report.get("sharpe_ratio_percentiles", {}),
            "risk_of_ruin_pct": mc_report.get("risk_of_ruin_pct", 0),
            "simulations_count": mc_report.get("num_simulations", 0),
        }

        # Generate HTML
        html_content = generate_tear_sheet_html(
            report=validation_report,
            strategy_params=req.params,
            symbol=req.symbol,
            timeframe=req.timeframe,
            exchange=req.exchange,
            trade_distribution=trade_distribution,
            equity_curves=equity_curves,
            monte_carlo_percentiles=mc_percentiles,
        )

        return HTMLResponse(content=html_content)

    except Exception as e:
        logger.error(f"[API] generate_tear_sheet_html error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/tear-sheet/json", dependencies=[Depends(get_api_key)])
@limiter.limit("10/minute")
async def generate_tear_sheet_json(
    request: Request, req: TearSheetRequest
) -> dict[str, Any]:
    """Generate and return a JSON tear sheet bundle for the given strategy configuration."""
    try:
        # Load candles for backtest and validation
        # "Auto (Optimal Lookback)" mode: use optimal lookback based on timeframe
        ts_days = get_optimal_lookback_days(req.timeframe)
        # Use async variant to avoid run_async() from main event loop
        sym_res = DataLoader.aresolve_canonical_symbol(req.symbol, live_mode=False)
        resolved_exchange, resolved_symbol = (
            await sym_res if inspect.isawaitable(sym_res) else sym_res
        )
        # Use async load_candles directly to avoid run_async() from main event loop
        candles_res = DataLoader.load_candles(
            exchange=resolved_exchange,
            symbol=resolved_symbol,
            timeframe=req.timeframe,
            limit=None,
            days=ts_days,
        )
        df_candles = (
            await candles_res if inspect.isawaitable(candles_res) else candles_res
        )

        # If no validation report provided, run validation
        if req.validation_report is None:
            strat_params = StrategyParams.from_dict(req.params)
            validator = StrategyValidator()
            report = validator.validate(
                df_candles=df_candles,
                params=strat_params,
                n_trials=req.n_trials,
                target_metric=req.target_metric,
                enable_multi_objective=req.enable_multi_objective,
                multi_objective_metrics=req.multi_objective_metrics,
                timeframe=req.timeframe,
            )
            validation_report = report.to_dict()
        else:
            validation_report = req.validation_report
            strat_params = StrategyParams.from_dict(req.params)

        # Run Monte Carlo for equity curves and percentiles

        engine = BacktestEngine(strat_params)
        bt_res = engine.run(df_candles)

        mc_cfg = MonteCarloConfig(
            num_simulations=req.mc_simulations, sample_with_replacement=True
        )
        mc_sim = MonteCarloSimulator(
            initial_capital=strat_params.initial_capital or 10000.0,
            trades=bt_res.trades,
            config=mc_cfg,
        )
        mc_report = mc_sim.run()

        # Build trade distribution
        pnls = [t.get("pnl", 0.0) for t in bt_res.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        trade_distribution = {
            "total_trades": len(pnls),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": (round(len(wins) / len(pnls) * 100, 1) if pnls else 0),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
            "profit_factor": (
                round(sum(wins) / abs(sum(losses)), 2) if losses else float("inf")
            ),
        }

        # Build equity curves (downsampled)
        equity_curves = mc_report.get(
            "equity_curves_percentiles", {"p5": [], "p50": [], "p95": []}
        )

        # Build Monte Carlo percentiles
        mc_percentiles = {
            "final_equity": mc_report.get("final_equity_percentiles", {}),
            "net_profit": mc_report.get("net_profit_percentiles", {}),
            "max_drawdown": mc_report.get("max_drawdown_percentiles", {}),
            "sharpe_ratio": mc_report.get("sharpe_ratio_percentiles", {}),
            "risk_of_ruin_pct": mc_report.get("risk_of_ruin_pct", 0),
            "simulations_count": mc_report.get("num_simulations", 0),
        }

        # Return JSON bundle
        return {
            "status": "success",
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "exchange": req.exchange,
            "generated_at": datetime.now(UTC).isoformat() + "Z",
            "report": validation_report,
            "strategy_params": req.params,
            "trade_distribution": trade_distribution,
            "equity_curves": equity_curves,
            "monte_carlo_percentiles": mc_percentiles,
        }

    except Exception as e:
        logger.error(f"[API] generate_tear_sheet_json error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
