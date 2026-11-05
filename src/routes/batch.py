"""
Batch Sweep Endpoints + Batch-to-Portfolio Export + all-metrics task.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

import json
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from src.app_limiter import limiter
from src.auth import get_api_key
from src.batch_optimizer import (
    BatchOptimizer,
    BatchOptimizerConfig,
    _update_state,
    cancel_batch,
    get_batch_state,
)
from src.config import StrategyParams, get_optimal_lookback_days
from src.data_loader import DataLoader
from src.logger import logger
from src.pg_database import (
    get_batch_result_pg,
    load_batch_results_pg,
    update_batch_result_validation_pg,
)

router = APIRouter(tags=["Batch Operations"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


# ---------------------------------------------------------------------------
# Batch Sweep Endpoints
# ---------------------------------------------------------------------------


class BatchOptimizeRequest(BaseModel):
    exchange: str = "kucoin"
    symbols: list[str] = ["BTC/USD", "ETH/USD", "SOL/USD"]
    timeframes: list[str] = ["5m", "15m", "1h"]
    days: int | None = 360
    n_trials: int = 100
    target_metric: str = "sharpe_ratio"
    strategy_mode: str = "auto"
    min_trades: int = 5

    # Search ranges (default to OptunaConfig defaults)
    fast_ema_min: int = 3
    fast_ema_max: int = 30
    slow_ema_min: int = 10
    slow_ema_max: int = 100
    trend_ema_min: int = 50
    trend_ema_max: int = 250
    volume_multiplier_min: float = 0.8
    volume_multiplier_max: float = 3.5
    atr_multiplier_min: float = 1.0
    atr_multiplier_max: float = 6.0
    risk_reward_min: float = 1.0
    risk_reward_max: float = 6.0
    vwap_slope_max_bound: float = 0.003
    n_jobs: int = 1
    n_startup_trials: int | None = None
    seed_base_params: bool = True
    multi_objective_metrics: list[str] | None = None
    enable_multi_objective: bool = False
    enable_hard_drawdown_constraint: bool = False
    max_drawdown_constraint_pct: float = 25.0
    enable_max_holding_bars: bool = False
    max_holding_bars_min: int = 4
    max_holding_bars_max: int = 96
    trade_direction: str = "long_only"


class BatchResultsDeleteRequest(BaseModel):
    ids: list[int]


class BatchResultsPurgeRequest(BaseModel):
    older_than_days: int = 30
    target_metric: str | None = None


class BatchResultsAutoValidateRequest(BaseModel):
    target_metric: str | None = None
    exchange: str = "kucoin"
    symbols: list[str] | None = None
    timeframes: list[str] | None = None
    n_trials: int | None = None
    criteria: dict[str, Any] | None = None


class BatchResultFavoriteRequest(BaseModel):
    favorite: bool


def _run_batch_task(cfg: BatchOptimizerConfig) -> None:
    """Background thread target — runs the full sweep without blocking the HTTP response."""
    import asyncio
    import traceback

    async def _run_optimizer():
        optimizer = BatchOptimizer(cfg)
        await optimizer.run()

    try:
        # Offload the entire batch optimization to a thread pool to avoid
        # blocking the main event loop (which must stay responsive for
        # API requests, WebSocket messages, and paper trading polling).
        # Create a new event loop for this thread to run the async work.
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            # Run the async function directly in this thread's event loop.
            # This runs the async work in this thread's event loop, not the main loop.
            loop.run_until_complete(_run_optimizer())
        finally:
            loop.close()
    except Exception as exc:
        import traceback

        tb = traceback.format_exc()

        from src.batch_optimizer import _update_state

        _update_state(running=False, error=str(exc), debug_error=tb)
        logger.error(f"[BatchOptimizer] _run_batch_task failed: {exc}\n{tb}")


@router.post("/api/batch_optimize", dependencies=[Depends(get_api_key)])
@limiter.limit("5/minute")
def start_batch_optimize(
    request: Request, req: BatchOptimizeRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Start a multi-coin/multi-timeframe Optuna sweep in the background."""
    # Immediately set running state so UI doesn't show stale "Complete" from previous run
    _update_state(
        running=True,
        done=0,
        total=0,
        current_combo="Starting...",
        combo_progress=0.0,
        error=None,
        cancelled=False,
    )

    state = get_batch_state()
    if state["running"]:
        # Double-check after setting our own state
        pass  # We just set it, so it's expected to be running
    cfg = BatchOptimizerConfig(
        symbols=req.symbols,
        timeframes=req.timeframes,
        exchange=req.exchange,
        days=req.days,
        n_trials=req.n_trials,
        target_metric=req.target_metric,
        strategy_mode=req.strategy_mode,
        min_trades=req.min_trades,
        trade_direction=req.trade_direction,
        # enable_multi_objective and multi_objective_metrics are set by
        # BatchOptimizerConfig.__post_init__ based on target_metric
        enable_multi_objective=req.enable_multi_objective,
        multi_objective_metrics=(
            req.multi_objective_metrics
            if req.multi_objective_metrics
            else (
                req.target_metric.split("|")
                if "|" in req.target_metric
                else ["sharpe_ratio", "max_drawdown_pct"]
            )
        ),
        enable_hard_drawdown_constraint=req.enable_hard_drawdown_constraint,
        max_drawdown_constraint_pct=req.max_drawdown_constraint_pct,
        enable_max_holding_bars=req.enable_max_holding_bars,
        max_holding_bars_min=req.max_holding_bars_min,
        max_holding_bars_max=req.max_holding_bars_max,
    )
    # Handle all_metrics target - expand to single metrics
    from src.batch_optimizer import ALL_CURATED_MULTI_PAIRS, ALL_SINGLE_METRICS

    if req.target_metric == "all_metrics":
        if len(req.timeframes) > 1:
            raise HTTPException(
                status_code=400,
                detail="all_metrics target only supports a single timeframe; use specific metric for multi-timeframe sweeps.",
            )
        # Generate a config for each single metric
        configs = []
        for metric in ALL_SINGLE_METRICS:
            metric_cfg = BatchOptimizerConfig(
                symbols=req.symbols,
                timeframes=req.timeframes,
                exchange=req.exchange,
                days=req.days,
                n_trials=req.n_trials,
                target_metric=metric,
                strategy_mode=req.strategy_mode,
                min_trades=req.min_trades,
                trade_direction=req.trade_direction,
                enable_multi_objective=False,
                multi_objective_metrics=None,
                enable_hard_drawdown_constraint=req.enable_hard_drawdown_constraint,
                max_drawdown_constraint_pct=req.max_drawdown_constraint_pct,
                enable_max_holding_bars=req.enable_max_holding_bars,
                max_holding_bars_min=req.max_holding_bars_min,
                max_holding_bars_max=req.max_holding_bars_max,
            )
            configs.append(metric_cfg)
        background_tasks.add_task(_run_all_metrics_batch_task, configs)
        total_combos = len(req.symbols) * len(req.timeframes) * len(ALL_SINGLE_METRICS)
        total_metrics = len(ALL_SINGLE_METRICS)
        return {
            "status": "started",
            "total_combos": total_combos,
            "total_metrics": total_metrics,
        }

    # Handle all_combinations target - expand to curated multi-objective pairs
    if req.target_metric == "all_combinations":
        if len(req.timeframes) > 1:
            raise HTTPException(
                status_code=400,
                detail="all_combinations target only supports a single timeframe; use specific metric for multi-timeframe sweeps.",
            )
        # Generate a config for each curated multi-objective pair
        configs = []
        for pair in ALL_CURATED_MULTI_PAIRS:
            pair_cfg = BatchOptimizerConfig(
                symbols=req.symbols,
                timeframes=req.timeframes,
                exchange=req.exchange,
                days=req.days,
                n_trials=req.n_trials,
                target_metric=f"{pair[0]}|{pair[1]}",
                strategy_mode=req.strategy_mode,
                min_trades=req.min_trades,
                trade_direction=req.trade_direction,
                enable_multi_objective=True,
                multi_objective_metrics=pair,
                enable_hard_drawdown_constraint=req.enable_hard_drawdown_constraint,
                max_drawdown_constraint_pct=req.max_drawdown_constraint_pct,
                enable_max_holding_bars=req.enable_max_holding_bars,
                max_holding_bars_min=req.max_holding_bars_min,
                max_holding_bars_max=req.max_holding_bars_max,
            )
            configs.append(pair_cfg)
        background_tasks.add_task(_run_all_metrics_batch_task, configs)
        total_combos = (
            len(req.symbols) * len(req.timeframes) * len(ALL_CURATED_MULTI_PAIRS)
        )
        total_pairs = len(ALL_CURATED_MULTI_PAIRS)
        return {
            "status": "started",
            "total_combos": total_combos,
            "total_pairs": total_pairs,
        }

    background_tasks.add_task(_run_batch_task, cfg)
    total = len(req.symbols) * len(req.timeframes)
    return {"status": "started", "total_combos": total}


@router.post(
    "/api/optimizer/batch_optimize",
    include_in_schema=False,
    dependencies=[Depends(get_api_key)],
)
@limiter.limit("5/minute")
def start_batch_optimize_alias(
    request: Request, req: BatchOptimizeRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Compatibility alias for /api/batch_optimize. DEPRECATED: use /api/batch_optimize instead."""
    import warnings

    warnings.warn(
        "/api/optimizer/batch_optimize is deprecated; use /api/batch_optimize instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return start_batch_optimize(request, req, background_tasks)


@router.get("/api/batch_status")
def get_batch_status() -> dict[str, Any]:
    """Poll the current batch sweep progress (error details sanitized)."""
    state = get_batch_state()
    # Sanitize: don't expose raw exception strings to clients (ema_vwap-3w1)
    # But include a debug_error field with full traceback for debugging
    if state.get("error"):
        state = {**state, "error": "An internal error occurred during the batch sweep."}
    return state


@router.get("/api/batch_status_debug", dependencies=[Depends(get_api_key)])
def get_batch_status_debug() -> dict[str, Any]:
    """Poll the current batch sweep progress with full error details for debugging."""
    state = get_batch_state()
    # Return full error details for debugging
    return state


@router.get(
    "/api/optimizer/status",
    include_in_schema=False,
    dependencies=[Depends(get_api_key)],
)
def get_optimizer_status_alias() -> dict[str, Any]:
    """Compatibility alias for /api/batch_status."""
    return get_batch_state()


@router.post("/api/batch_cancel", dependencies=[Depends(get_api_key)])
def cancel_batch_endpoint() -> dict[str, Any]:
    """Cancel any running batch sweep."""
    return {"cancelled": cancel_batch()}


@router.get("/api/batch_results", dependencies=[Depends(get_api_key)])
async def get_batch_results(
    target_metric: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Return the leaderboard from the batch_results table, sorted by best_value."""
    rows = await load_batch_results_pg(target_metric=target_metric, limit=limit)
    return {"status": "success", "count": len(rows), "results": rows}


@router.delete("/api/batch_results", dependencies=[Depends(get_api_key)])
def delete_batch_results(req: BatchResultsDeleteRequest) -> dict[str, Any]:
    """Delete selected batch sweep results by database ID."""
    if not req.ids or any(result_id <= 0 for result_id in req.ids):
        raise HTTPException(
            status_code=400, detail="Provide one or more valid result IDs."
        )
    deleted = _st()._batch_db.delete_batch_results(req.ids)
    return {"status": "success", "deleted": deleted}


class BatchResultValidationRequest(BaseModel):
    status: str
    report_json: str


@router.patch(
    "/api/batch_results/{result_id}/favorite", dependencies=[Depends(get_api_key)]
)
def set_batch_result_favorite(
    result_id: int, req: BatchResultFavoriteRequest
) -> dict[str, Any]:
    """Set the saved/favorite state for one batch sweep result."""
    if result_id <= 0:
        raise HTTPException(status_code=400, detail="Provide a valid result ID.")
    updated = _st()._batch_db.set_batch_result_favorite(result_id, req.favorite)
    if not updated:
        raise HTTPException(status_code=404, detail="Batch result not found.")
    return {"status": "success", "id": result_id, "favorite": req.favorite}


class BatchResultDeployedRequest(BaseModel):
    deployed: bool


@router.patch(
    "/api/batch_results/{result_id}/deployed", dependencies=[Depends(get_api_key)]
)
def set_batch_result_deployed(
    result_id: int, req: BatchResultDeployedRequest
) -> dict[str, Any]:
    """Set the deployed status for one batch sweep result."""
    if result_id <= 0:
        raise HTTPException(status_code=400, detail="Provide a valid result ID.")
    updated = _st()._batch_db.set_batch_result_deployed(result_id, req.deployed)
    if not updated:
        raise HTTPException(status_code=404, detail="Batch result not found.")
    return {"status": "success", "id": result_id, "deployed": req.deployed}


@router.patch(
    "/api/batch_results/{result_id}/import", dependencies=[Depends(get_api_key)]
)
async def import_batch_result_as_profile(
    result_id: int,
    deploy: bool = True,
) -> dict[str, Any]:
    """Import a validated batch sweep result as a paper trading profile.
    When deploy=True, the profile is marked as deployed (ready for paper trading).
    """
    if result_id <= 0:
        raise HTTPException(status_code=400, detail="Provide a valid result ID.")

    # Get the batch result
    batch_result = await get_batch_result_pg(result_id)
    if not batch_result:
        raise HTTPException(status_code=404, detail="Batch result not found.")

    # Import as paper profile
    try:
        profile_id = await _st()._paper_profiles.import_batch_result_as_profile(
            exchange=batch_result["exchange"],
            symbol=batch_result["symbol"],
            timeframe=batch_result["timeframe"],
            strategy_mode=batch_result["strategy_mode"],
            target_metric=batch_result["target_metric"],
            best_value=batch_result["best_value"],
            best_params=batch_result["best_params"],
            risk_per_trade_pct=(
                batch_result["best_params"].get("risk_per_trade_pct")
                if isinstance(batch_result["best_params"], dict)
                else None
            ),
            is_active=True,
        )

        if deploy:
            _st()._paper_profiles.set_active(profile_id, is_active=True, deployed=True)

        return {
            "status": "success",
            "profile_id": profile_id,
            "deployed": deploy,
            "message": f"Batch result {result_id} imported as paper profile {profile_id}",
        }
    except Exception as e:
        logger.error(f"[API] import_batch_result_as_profile error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/batch_results/auto-validate", dependencies=[Depends(get_api_key)])
async def auto_validate_batch_results(
    req: BatchResultsAutoValidateRequest,
) -> dict[str, Any]:
    """Auto-validate all saved batch results (or a filtered subset) after a sweep.

    Runs the 3-gate robustness check on each result and persists the
    validation_status / validation_json back to the batch_results table.
    Designed to be triggered by the Batch Sweep tab's Auto-Validate checkbox.

    All PG access goes through run_async (submits to the app main loop) so
    pools stay loop-bound even when invoked from a foreign loop (tests,
    nested runtimes). Candle loads are already sync-bridged.
    """
    from src.sync_bridge import run_pg
    from src.validator import StrategyValidator, ValidationCriteria

    try:
        # Load results to validate (optionally filtered by target_metric, symbols, timeframes).
        # If symbols/timeframes provided, filter to only the recent sweep results.
        # run_pg: direct await on the app main loop (pools stay loop-bound),
        # wrapped to the main loop from foreign loops (tests).
        load_kwargs = {
            "target_metric": req.target_metric,
            "limit": 1000,
        }
        if req.symbols is not None:
            load_kwargs["symbols"] = req.symbols
        if req.timeframes is not None:
            load_kwargs["timeframes"] = req.timeframes
        rows = await run_pg(load_batch_results_pg(**load_kwargs))
        if not rows:
            return {
                "status": "success",
                "validated_count": 0,
                "message": "No results to validate.",
            }

        validated = 0
        errors: list[str] = []
        for row in rows:
            try:
                params_dict = json.loads(row.get("best_params") or "{}")
                exchange = row.get("exchange", req.exchange or "kucoin")
                symbol = row.get("symbol", "BTC/USD")
                timeframe = row.get("timeframe", "5m")
                target_metric = (
                    row.get("target_metric") or req.target_metric or "sharpe_ratio"
                )
                is_multi = bool(target_metric and "|" in target_metric)
                multi_metrics = (
                    list(filter(bool, target_metric.split("|"))) if is_multi else None
                )
                resolved_target = multi_metrics[0] if is_multi else target_metric

                # Load candles for validation - use optimal lookback (Auto mode)
                # Detect "Auto (Optimal Lookback)" mode: limit=500, days=None -> use optimal lookback
                val_limit = 500
                val_days = None
                if val_limit == 500 and val_days is None:
                    val_days = get_optimal_lookback_days(timeframe)
                    val_limit = None  # let DataLoader compute from days

                # Async candle load: this endpoint runs ON the app main loop,
                # where the sync bridge (run_async) is forbidden — it would
                # raise the loop-guard and skip every result (the
                # "Validated 0 results" bug).
                df_candles = await DataLoader.load_candles(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=val_limit,
                    days=val_days,
                )
                strat_params = (
                    StrategyParams.from_dict(params_dict)
                    if params_dict
                    else StrategyParams()
                )
                crit = (
                    ValidationCriteria(**req.criteria)
                    if req.criteria
                    else ValidationCriteria()
                )
                validator = StrategyValidator(crit)
                report = validator.validate(
                    df_candles=df_candles,
                    params=strat_params,
                    n_trials=req.n_trials,
                    target_metric=resolved_target,
                    enable_multi_objective=is_multi,
                    multi_objective_metrics=multi_metrics,
                    timeframe=timeframe,
                )
                # Gate 1 key is "gate1_backtest" in ValidationReport
                gate_results = getattr(report, "gate_results", None)
                g1 = (
                    gate_results.get("gate1_backtest")
                    if isinstance(gate_results, dict)
                    else None
                )
                g1_metrics = getattr(g1, "metrics", None) if g1 else None
                update_kwargs = {
                    "result_id": row["id"],
                    "status": report.status,
                    "report_json": json.dumps(report.to_dict()),
                }
                if g1_metrics is not None:
                    update_kwargs["metrics"] = g1_metrics
                await run_pg(update_batch_result_validation_pg(**update_kwargs))
                validated += 1
            except Exception as exc:
                errors.append(
                    f"{row.get('symbol', '?')}/{row.get('timeframe', '?')}: {exc}"
                )
                logger.warning(
                    f"[API] auto_validate_batch_results: skipping result {row.get('id')}: {exc}"
                )

        return {
            "status": "success",
            "validated_count": validated,
            "total_results": len(rows),
            "errors": errors,
        }
    except Exception as e:
        logger.error(f"[API] auto_validate_batch_results error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch(
    "/api/batch_results/{result_id}/validation", dependencies=[Depends(get_api_key)]
)
def update_batch_result_validation(
    result_id: int, req: BatchResultValidationRequest
) -> dict[str, Any]:
    """Save validation status and report JSON for a batch result row."""
    if result_id <= 0:
        raise HTTPException(status_code=400, detail="Provide a valid result ID.")
    updated = _st()._batch_db.update_batch_result_validation(
        result_id=result_id, status=req.status, report_json=req.report_json
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Batch result not found.")
    return {"status": "success", "id": result_id, "validation_status": req.status}


@router.post("/api/batch_results/purge", dependencies=[Depends(get_api_key)])
def purge_batch_results(req: BatchResultsPurgeRequest) -> dict[str, Any]:
    """Purge stale batch sweep results by age, optionally limited to one metric."""
    if req.older_than_days < 1:
        raise HTTPException(
            status_code=400, detail="older_than_days must be at least 1."
        )
    deleted = _st()._batch_db.purge_batch_results(
        older_than_days=req.older_than_days, target_metric=req.target_metric
    )
    return {"status": "success", "deleted": deleted}


# -------------------------------------------------------------------------
# Batch-to-Portfolio Export
# -------------------------------------------------------------------------


class BatchToPortfolioRequest(BaseModel):
    """Request to export batch results to portfolio rebalancer."""

    result_ids: list[int]
    weighting_method: Literal[
        "sharpe_weighted", "risk_parity", "equal_weight", "risk_budget"
    ] = "sharpe_weighted"
    max_assets: int = 10
    min_sharpe: float = 0.5
    min_trades: int = 5
    # Risk budget parameters (used when weighting_method == "risk_budget")
    max_drawdown_per_asset_pct: float = 35.0
    max_single_asset_weight: float = 0.30
    mc_simulations: int = 500


@router.post(
    "/api/batch_results/export_to_portfolio",
    dependencies=[Depends(get_api_key)],
)
async def export_batch_to_portfolio(req: BatchToPortfolioRequest) -> dict[str, Any]:
    """Export selected batch sweep results to portfolio rebalancer with computed target weights."""
    try:
        # Load the selected batch results
        all_rows = await load_batch_results_pg(limit=1000)
        selected = [r for r in all_rows if r["id"] in req.result_ids]

        if not selected:
            raise HTTPException(
                status_code=404, detail="No matching batch results found"
            )

        # Filter by minimum criteria
        filtered = [
            r
            for r in selected
            if r.get("trade_count", 0) >= req.min_trades
            and r.get("sharpe_ratio", 0) >= req.min_sharpe
        ]

        if not filtered:
            raise HTTPException(
                status_code=400,
                detail=f"No results meet minimum criteria (min_trades={req.min_trades}, min_sharpe={req.min_sharpe})",
            )

        # Sort by Sharpe ratio descending
        filtered.sort(key=lambda x: x.get("sharpe_ratio", 0), reverse=True)

        # Take top N
        top_results = filtered[: req.max_assets]

        # Compute target weights based on weighting method
        target_weights = {}

        if req.weighting_method == "sharpe_weighted":
            # Weight proportional to Sharpe ratio
            sharpes = [r.get("sharpe_ratio", 0) for r in top_results]
            total = sum(s for s in sharpes if s > 0)
            if total > 0:
                for r in top_results:
                    sym = r["symbol"]
                    sharpe = max(0, r.get("sharpe_ratio", 0))
                    target_weights[sym] = sharpe / total
            else:
                # Fallback to equal weight
                for r in top_results:
                    target_weights[r["symbol"]] = 1.0 / len(top_results)

        elif req.weighting_method == "risk_parity":
            # Risk parity: weight inversely proportional to volatility (approximated by 1/max_dd)
            risks = [
                1.0 / max(0.1, r.get("max_drawdown_pct", 10.0)) for r in top_results
            ]
            total = sum(risks)
            for i, r in enumerate(top_results):
                target_weights[r["symbol"]] = risks[i] / total

        else:  # equal_weight
            weight = 1.0 / len(top_results)
            for r in top_results:
                target_weights[r["symbol"]] = weight

        if req.weighting_method == "risk_budget":
            # Risk budget: use PortfolioRiskBudgetEngine with covariance-based
            # risk parity weights and per-asset max drawdown constraints.
            from src.portfolio.correlation import CrossAssetCorrelationEngine
            from src.portfolio.portfolio_risk_budget import (
                PortfolioRiskBudgetEngine,
                RiskBudgetConfig,
            )

            rb_symbols = [r["symbol"] for r in top_results]
            rb_returns = CrossAssetCorrelationEngine.generate_synthetic_returns(
                rb_symbols
            )
            rb_asset_metrics = {
                r["symbol"]: {
                    "max_drawdown_pct": r.get("max_drawdown_pct", 10.0),
                    "sharpe_ratio": r.get("sharpe_ratio", 0.0),
                }
                for r in top_results
            }
            rb_config = RiskBudgetConfig(
                risk_parity=True,
                max_drawdown_per_asset_pct=req.max_drawdown_per_asset_pct,
                max_single_asset_weight=req.max_single_asset_weight,
                mc_simulations=req.mc_simulations,
            )
            rb_result = PortfolioRiskBudgetEngine.compute_risk_budget(
                symbols=rb_symbols,
                asset_metrics=rb_asset_metrics,
                returns_df=rb_returns,
                config=rb_config,
            )
            target_weights = rb_result.weights

        return {
            "status": "success",
            "weighting_method": req.weighting_method,
            "target_weights": target_weights,
            "selected_results": [
                {
                    "id": r["id"],
                    "symbol": r["symbol"],
                    "timeframe": r["timeframe"],
                    "sharpe_ratio": r.get("sharpe_ratio", 0),
                    "max_drawdown_pct": r.get("max_drawdown_pct", 0),
                    "trade_count": r.get("trade_count", 0),
                    "total_return_pct": r.get("total_return_pct", 0),
                    "weight": target_weights.get(r["symbol"], 0),
                }
                for r in top_results
            ],
            "count": len(top_results),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] export_batch_to_portfolio error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------------------------------------------------


def _run_all_metrics_batch_task(configs: list) -> None:
    import asyncio
    import traceback

    from src.batch_optimizer import (
        BatchOptimizer,
        _update_state,
        get_batch_state,
        reset_batch_state,
    )

    reset_batch_state()
    # Total combos = metrics * symbols * timeframes
    combos_per_metric = (
        len(configs[0].symbols) * len(configs[0].timeframes) if configs else 0
    )
    total_all = combos_per_metric * len(configs)
    _update_state(running=True, total=total_all, done=0, current_combo="")

    # Create a single event loop for the entire batch sweep to reuse
    # connection pools and avoid "too many clients" errors.
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        for i, cfg in enumerate(configs):
            if get_batch_state().get("cancelled"):
                _update_state(running=False, error="Batch sweep cancelled")
                return
            label = f"[{cfg.target_metric} — {i+1}/{len(configs)}] "
            try:
                # Offload each metric's optimization to thread pool to avoid
                # blocking the main event loop.
                async def _run_optimizer(
                    _cfg=cfg, _i=i, _total=total_all, _label=label
                ):
                    optimizer = BatchOptimizer(_cfg)
                    res = optimizer.run(
                        reset_state=(_i == 0),
                        done_offset=_i * combos_per_metric,
                        total_override=_total,
                        combo_label_prefix=_label,
                        mark_complete=(_i == len(configs) - 1),
                    )
                    import inspect

                    if inspect.isawaitable(res):
                        await res

                # Run the async function in the shared event loop.
                # This reuses the same connection pool for all metrics.
                loop.run_until_complete(_run_optimizer())
            except Exception as exc:
                import traceback

                tb = traceback.format_exc()
                _update_state(
                    running=False, error=f"Batch sweep failed: {exc}", debug_error=tb
                )
                logger.error(
                    f"[BatchOptimizer] _run_all_metrics_batch_task failed: {exc}\n{tb}"
                )
                return
            _update_state(done=(i + 1) * combos_per_metric, current_combo=label)
    finally:
        loop.close()
    _update_state(
        running=False, done=total_all, total=total_all, current_combo="Complete"
    )
