"""
Backtest, Optuna Optimization, WFO, Monte Carlo & Strategy Validation Endpoints.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

from typing import Any

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from src.app_limiter import limiter
from src.auth import get_api_key
from src.backtester import BacktestEngine
from src.config import (
    MonteCarloConfig,
    OptunaConfig,
    StrategyParams,
    WFOConfig,
    get_optimal_lookback_days,
)
from src.data_loader import DataLoader
from src.logger import logger
from src.monte_carlo import MonteCarloSimulator
from src.optimizer import OptunaOptimizer
from src.strategy import EmaVwapStrategy
from src.tasks import TaskManager, global_task_manager
from src.validator import StrategyValidator, ValidationCriteria
from src.wfo import WalkForwardEngine

router = APIRouter(tags=["Backtest & Optimization"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


class BaseStrategyRequest(BaseModel):
    exchange: str = "gemini"
    symbol: str = "BTC/USD"
    timeframe: str = "5m"
    limit: int = 500
    days: int | None = None

    # Strategy Parameters
    strategy_mode: str = "crossover"
    trade_direction: str = "long_only"
    fast_ema: int = 9
    slow_ema: int = 21
    trend_ema: int = 50
    vwap_slope_min: float = 0.0001
    vwap_slope_lookback: int = 5
    volume_filter_enabled: bool = True
    volume_multiplier: float = 1.2
    volume_sma_period: int = 20
    pullback_tolerance_pct: float = 0.3
    stop_loss_type: str = "vwap"
    vwap_stop_offset_pct: float = 0.1
    atr_period: int = 14
    atr_multiplier: float = 2.0
    risk_reward_ratio: float = 2.0
    risk_per_trade_pct: float = 1.0
    max_holding_bars: int | None = None
    initial_capital: float = 10000.0
    maker_fee_pct: float = 0.05
    taker_fee_pct: float = 0.075
    slippage_pct: float = 0.05

    # Multi-Asset and Intraday Margin Framework
    asset_type: str = "crypto"
    session_mode: str = "crypto"
    vwap_anchor: str = "D"
    allow_fractional_shares: bool = True
    enforce_margin_calls: bool = False
    min_margin_equity: float = 2000.0
    long_buying_power_ratio: float = 4.0
    short_buying_power_ratio: float = 2.0
    position_size: float = 0.0

    @field_validator("risk_per_trade_pct")
    @classmethod
    def validate_risk_per_trade_pct(cls, v: float) -> float:
        if not (0 < v <= 100):
            raise ValueError("risk_per_trade_pct must be between 0 and 100")
        return v

    @field_validator("risk_reward_ratio")
    @classmethod
    def validate_risk_reward_ratio(cls, v: float) -> float:
        if not (v > 0):
            raise ValueError("risk_reward_ratio must be positive")
        return v

    @field_validator("long_buying_power_ratio", "short_buying_power_ratio")
    @classmethod
    def validate_buying_power_ratio(cls, v: float) -> float:
        if not (v > 1):
            raise ValueError("buying_power_ratio must be greater than 1")
        return v

    @field_validator("min_margin_equity")
    @classmethod
    def validate_min_margin_equity(cls, v: float) -> float:
        if not (v >= 0):
            raise ValueError("min_margin_equity must be non-negative")
        return v

    @field_validator(
        "fast_ema", "slow_ema", "trend_ema", "atr_period", "volume_sma_period"
    )
    @classmethod
    def validate_ema_periods(cls, v: int) -> int:
        if not (v > 0):
            raise ValueError("EMA/ATR/volume periods must be positive")
        return v

    @field_validator("initial_capital", "min_margin_equity")
    @classmethod
    def validate_positive_floats(cls, v: float) -> float:
        if not (v > 0):
            raise ValueError("initial_capital and min_margin_equity must be positive")
        return v

    @field_validator("maker_fee_pct", "taker_fee_pct", "slippage_pct")
    @classmethod
    def validate_fee_pct(cls, v: float) -> float:
        if not (0 <= v <= 10):
            raise ValueError("fee/slippage percentages must be between 0 and 10")
        return v

    @field_validator("volume_multiplier")
    @classmethod
    def validate_volume_multiplier(cls, v: float) -> float:
        if not (0.1 <= v <= 10):
            raise ValueError("volume_multiplier must be between 0.1 and 10")
        return v

    @field_validator("pullback_tolerance_pct")
    @classmethod
    def validate_pullback_tolerance(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError("pullback_tolerance_pct must be between 0 and 100")
        return v

    @field_validator("vwap_stop_offset_pct")
    @classmethod
    def validate_vwap_stop_offset(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError("vwap_stop_offset_pct must be between 0 and 100")
        return v

    @field_validator("atr_multiplier")
    @classmethod
    def validate_atr_multiplier(cls, v: float) -> float:
        if not (0.1 <= v <= 10):
            raise ValueError("atr_multiplier must be between 0.1 and 10")
        return v

    @field_validator("risk_reward_ratio")
    @classmethod
    def validate_risk_reward(cls, v: float) -> float:
        if not (0.1 <= v <= 100):
            raise ValueError("risk_reward_ratio must be between 0.1 and 100")
        return v

    @field_validator("max_holding_bars")
    @classmethod
    def validate_max_holding_bars(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("max_holding_bars must be positive if set")
        return v


class BacktestRequest(BaseStrategyRequest):
    pass


def build_strategy_params(req: BaseStrategyRequest) -> StrategyParams:
    """Build StrategyParams from request, automatically preserving all evaluated parameters."""
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return StrategyParams.from_dict(data)


@router.post("/api/backtest", dependencies=[Depends(get_api_key)])
@limiter.limit("30/minute")
def run_backtest(request: Request, req: BacktestRequest) -> dict[str, Any]:
    try:
        # "Auto (Optimal Lookback)" mode: frontend sends limit=500, days=None
        # Detect this and use optimal lookback based on timeframe
        backtest_days = req.days
        backtest_limit = req.limit
        if backtest_days is None and backtest_limit == 500:
            # Auto mode: use optimal lookback days for the timeframe
            backtest_days = get_optimal_lookback_days(req.timeframe)
            backtest_limit = None  # let DataLoader compute from days

        # Load Candles from Database Cache or Exchange API
        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=backtest_limit,
            days=backtest_days,
        )

        # Build StrategyParams
        params = build_strategy_params(req)

        # Compute indicators and strategy signals
        strategy = EmaVwapStrategy(params)
        df_signals = strategy.generate_signals(df_candles)

        # Run Backtest
        engine = BacktestEngine(params)
        result = engine.run(df_candles)

        # Prepare chart candles response
        chart_data = []
        for _, row in df_signals.iterrows():
            chart_data.append(
                {
                    "timestamp": str(row["timestamp"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "vwap": float(row["vwap"]) if not pd.isna(row["vwap"]) else None,
                    "ema_fast": (
                        float(row[f"ema_{params.fast_ema}"])
                        if f"ema_{params.fast_ema}" in row
                        and not pd.isna(row[f"ema_{params.fast_ema}"])
                        else None
                    ),
                    "ema_slow": (
                        float(row[f"ema_{params.slow_ema}"])
                        if f"ema_{params.slow_ema}" in row
                        and not pd.isna(row[f"ema_{params.slow_ema}"])
                        else None
                    ),
                    "ema_trend": (
                        float(row[f"ema_{params.trend_ema}"])
                        if f"ema_{params.trend_ema}" in row
                        and not pd.isna(row[f"ema_{params.trend_ema}"])
                        else None
                    ),
                    "signal": int(row["signal"]),
                }
            )

        return {
            "status": "success",
            "symbol": req.symbol,
            "exchange": req.exchange,
            "timeframe": req.timeframe,
            "params": params.to_dict(),
            "metrics": result.to_dict(),
            "chart_data": chart_data,
        }
    except Exception as e:
        logger.error(f"[API] run_backtest error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


class OptimizeRequest(BaseStrategyRequest):
    target_metric: str = "sharpe_ratio"
    n_trials: int = 30
    timeout_seconds: int | None = None
    min_trades: int = 3
    n_startup_trials: int | None = None
    n_jobs: int = 1
    multivariate: bool = True
    seed_base_params: bool = True
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] | None = None
    enable_hard_drawdown_constraint: bool = False
    max_drawdown_constraint_pct: float = 25.0
    enable_max_holding_bars: bool = False
    max_holding_bars_min: int = 4
    max_holding_bars_max: int = 96
    fast_ema_min: int | None = None
    fast_ema_max: int | None = None
    slow_ema_min: int | None = None
    slow_ema_max: int | None = None
    trend_ema_min: int | None = None
    trend_ema_max: int | None = None
    vwap_slope_min_bound: float | None = None
    vwap_slope_max_bound: float | None = None
    volume_multiplier_min: float | None = None
    volume_multiplier_max: float | None = None
    atr_multiplier_min: float | None = None
    atr_multiplier_max: float | None = None
    risk_reward_min: float | None = None
    risk_reward_max: float | None = None
    pullback_tolerance_min: float | None = None
    pullback_tolerance_max: float | None = None
    vwap_stop_offset_min: float | None = None
    vwap_stop_offset_max: float | None = None
    execution_mode: str = "sequential"


class WFORequest(BaseStrategyRequest):
    num_windows: int = 0  # 0 = auto-scale from candle count and n_trials_total
    in_sample_ratio: float = 0.7
    window_type: str = "rolling"
    trials_per_window: int = 0  # 0 = auto-scale from n_trials_total / num_windows
    n_trials_total: int = (
        0  # total Optuna budget used to derive trials_per_window when auto
    )


class MonteCarloRequest(BaseStrategyRequest):
    num_simulations: int = 1000
    sample_with_replacement: bool = True


@router.post("/api/optimize", dependencies=[Depends(get_api_key)])
def run_optimization(
    req: OptimizeRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Run Optuna optimization in the background; returns task_id immediately."""
    try:
        # Build config so we can validate synchronously before dispatching
        base_params = build_strategy_params(req)
        opt_config = OptunaConfig(
            target_metric=req.target_metric,
            n_trials=req.n_trials,
            timeout_seconds=req.timeout_seconds,
            min_trades=req.min_trades,
            n_startup_trials=req.n_startup_trials,
            n_jobs=req.n_jobs,
            multivariate=req.multivariate,
            seed_base_params=req.seed_base_params,
            enable_multi_objective=req.enable_multi_objective,
            multi_objective_metrics=(
                req.multi_objective_metrics
                if req.multi_objective_metrics is not None
                else ["sharpe_ratio", "max_drawdown_pct"]
            ),
            fast_ema_min=req.fast_ema_min if req.fast_ema_min is not None else 3,
            fast_ema_max=req.fast_ema_max if req.fast_ema_max is not None else 30,
            slow_ema_min=req.slow_ema_min if req.slow_ema_min is not None else 10,
            slow_ema_max=req.slow_ema_max if req.slow_ema_max is not None else 100,
            trend_ema_min=req.trend_ema_min if req.trend_ema_min is not None else 50,
            trend_ema_max=req.trend_ema_max if req.trend_ema_max is not None else 250,
            vwap_slope_min=(
                req.vwap_slope_min_bound
                if req.vwap_slope_min_bound is not None
                else 0.0
            ),
            vwap_slope_max=(
                req.vwap_slope_max_bound
                if req.vwap_slope_max_bound is not None
                else 0.003
            ),
            volume_multiplier_min=(
                req.volume_multiplier_min
                if req.volume_multiplier_min is not None
                else 0.8
            ),
            volume_multiplier_max=(
                req.volume_multiplier_max
                if req.volume_multiplier_max is not None
                else 3.5
            ),
            atr_multiplier_min=(
                req.atr_multiplier_min if req.atr_multiplier_min is not None else 1.0
            ),
            atr_multiplier_max=(
                req.atr_multiplier_max if req.atr_multiplier_max is not None else 6.0
            ),
            risk_reward_min=(
                req.risk_reward_min if req.risk_reward_min is not None else 1.0
            ),
            risk_reward_max=(
                req.risk_reward_max if req.risk_reward_max is not None else 6.0
            ),
            pullback_tolerance_min=(
                req.pullback_tolerance_min
                if req.pullback_tolerance_min is not None
                else 0.05
            ),
            pullback_tolerance_max=(
                req.pullback_tolerance_max
                if req.pullback_tolerance_max is not None
                else 1.5
            ),
            vwap_stop_offset_min=(
                req.vwap_stop_offset_min
                if req.vwap_stop_offset_min is not None
                else 0.02
            ),
            vwap_stop_offset_max=(
                req.vwap_stop_offset_max
                if req.vwap_stop_offset_max is not None
                else 0.8
            ),
            enable_hard_drawdown_constraint=req.enable_hard_drawdown_constraint,
            max_drawdown_constraint_pct=req.max_drawdown_constraint_pct,
            enable_max_holding_bars=req.enable_max_holding_bars,
            max_holding_bars_min=req.max_holding_bars_min,
            max_holding_bars_max=req.max_holding_bars_max,
            execution_mode=req.execution_mode,
        )
    except Exception as e:
        logger.error(f"[API] run_optimization config error: {e}")
        raise HTTPException(status_code=400, detail="Internal server error")

    def _optimize_worker(task_id: str, tm: TaskManager) -> dict[str, Any]:
        try:
            # "Auto (Optimal Lookback)" mode: frontend sends limit=500, days=None
            # Detect this and use optimal lookback based on timeframe
            opt_limit = req.limit
            opt_days = req.days
            if opt_days is None and opt_limit == 500:
                # Auto mode: use optimal lookback days for the timeframe
                opt_days = get_optimal_lookback_days(req.timeframe)
                opt_limit = None  # let DataLoader compute from days

            df_candles = DataLoader.load_candles(
                exchange=req.exchange,
                symbol=req.symbol,
                timeframe=req.timeframe,
                limit=opt_limit,
                days=opt_days,
            )
            tm.update_progress(task_id, 0.1, "Loaded candles")
            optimizer = OptunaOptimizer(base_params=base_params, config=opt_config)
            tm.update_progress(task_id, 0.2, "Optimizing")
            res = optimizer.optimize(df_candles)
            tm.update_progress(task_id, 1.0, "Complete")
            return {"status": "success", **res}
        except Exception as e:
            logger.error(f"[API] run_optimization worker error: {e}")
            raise

    task_id = global_task_manager.run_in_background("optimize", _optimize_worker)
    return {"status": "started", "task_id": task_id}


@router.post(
    "/api/optimizer/optimize",
    include_in_schema=False,
    dependencies=[Depends(get_api_key)],
)
def run_optimization_alias(
    req: OptimizeRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Compatibility alias for /api/optimize."""
    return run_optimization(req, background_tasks)


@router.post("/api/walk_forward", dependencies=[Depends(get_api_key)])
def run_walk_forward(req: WFORequest) -> dict[str, Any]:
    try:
        # WFO requires sufficient data for meaningful walk-forward windows.
        # Default to 365 days (1 year) instead of limit=500 which is only ~1.7 days for 5m.
        # Minimum 150 days needed for 3 windows; 365 gives ~7 windows; 500 gives 10 windows.
        wfo_days = req.days
        if wfo_days is None:
            # If user explicitly set limit > 500, respect it; otherwise default to 365 days
            if req.limit and req.limit > 500:
                wfo_days = None  # will use limit
            else:
                wfo_days = 365

        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=req.limit if wfo_days is None else None,
            days=wfo_days,
        )
        base_params = build_strategy_params(req)

        # Infer bars per day from timeframe string (e.g. "5m", "15m", "1h", "4h")
        _tf_to_bpd: dict[str, float] = {
            "1m": 1440.0,
            "3m": 480.0,
            "5m": 288.0,
            "15m": 96.0,
            "30m": 48.0,
            "1h": 24.0,
            "2h": 12.0,
            "4h": 6.0,
            "6h": 4.0,
            "8h": 3.0,
            "12h": 2.0,
            "1d": 1.0,
        }
        bars_per_day = _tf_to_bpd.get(req.timeframe.lower(), 288.0)

        wfo_config = WFOConfig(
            num_windows=req.num_windows,
            in_sample_ratio=req.in_sample_ratio,
            window_type=req.window_type,  # type: ignore
            trials_per_window=req.trials_per_window,
        )

        n_trials_budget = req.n_trials_total if req.n_trials_total > 0 else 30
        engine = WalkForwardEngine(
            base_params=base_params,
            wfo_config=wfo_config,
            n_trials_total=n_trials_budget,
            bars_per_day=bars_per_day,
            timeframe=req.timeframe,
        )

        res = engine.run(df_candles)
        return {
            "status": "success",
            "symbol": req.symbol,
            "exchange": req.exchange,
            "timeframe": req.timeframe,
            "results": res,
        }
    except Exception as e:
        logger.error(f"[API] run_walk_forward error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/monte_carlo", dependencies=[Depends(get_api_key)])
def run_monte_carlo(req: MonteCarloRequest) -> dict[str, Any]:
    try:
        # "Auto (Optimal Lookback)" mode: frontend sends limit=500, days=None
        # Detect this and use optimal lookback based on timeframe
        mc_limit = req.limit
        mc_days = req.days
        if mc_days is None and mc_limit == 500:
            # Auto mode: use optimal lookback days for the timeframe
            mc_days = get_optimal_lookback_days(req.timeframe)
            mc_limit = None  # let DataLoader compute from days

        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=mc_limit,
            days=mc_days,
        )
        params = build_strategy_params(req)
        backtest_res = BacktestEngine(params).run(df_candles)
        mc_config = MonteCarloConfig(
            num_simulations=req.num_simulations,
            sample_with_replacement=req.sample_with_replacement,
        )
        simulator = MonteCarloSimulator(
            initial_capital=req.initial_capital,
            trades=backtest_res.trades,
            config=mc_config,
        )
        res = simulator.run()
        return {
            "status": "success",
            "symbol": req.symbol,
            "exchange": req.exchange,
            "timeframe": req.timeframe,
            "results": res,
        }
    except Exception as e:
        logger.error(f"[API] run_monte_carlo error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error during Monte Carlo simulation",
        )


class ValidateStrategyRequest(BaseModel):
    exchange: str = "kucoin"
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    limit: int = 500
    days: int | None = None
    params: dict[str, Any] = {}
    criteria: dict[str, Any] | None = None
    n_trials: int = (
        250  # Optuna trial budget for Gate 2 WFO (default 250, allows >=50/window)
    )
    target_metric: str = (
        "sharpe_ratio"  # Metric that was optimized; informs Gate 1 scoring
    )
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] | None = None


@router.post("/api/strategy/validate", dependencies=[Depends(get_api_key)])
def validate_strategy_endpoint(req: ValidateStrategyRequest) -> dict[str, Any]:
    """
    Automated multi-stage robustness validation (Backtest + WFO + Monte Carlo)
    for a strategy configuration. Returns structured report and PASS/CAUTION/FAIL status.
    """
    try:
        # "Auto (Optimal Lookback)" mode: frontend sends limit=500, days=None
        # Detect this and use optimal lookback based on timeframe
        val_limit = req.limit
        val_days = req.days
        if val_days is None and val_limit == 500:
            # Auto mode: use optimal lookback days for the timeframe
            val_days = get_optimal_lookback_days(req.timeframe)
            val_limit = None  # let DataLoader compute from days

        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=val_limit,
            days=val_days,
        )

        strat_params = (
            StrategyParams.from_dict(req.params) if req.params else StrategyParams()
        )
        crit = (
            ValidationCriteria(**req.criteria) if req.criteria else ValidationCriteria()
        )
        validator = StrategyValidator(crit)
        report = validator.validate(
            df_candles=df_candles,
            params=strat_params,
            n_trials=req.n_trials,
            target_metric=req.target_metric,
            enable_multi_objective=req.enable_multi_objective,
            multi_objective_metrics=req.multi_objective_metrics,
            timeframe=req.timeframe,
        )

        return {
            "status": "success",
            "symbol": req.symbol,
            "exchange": req.exchange,
            "timeframe": req.timeframe,
            "report": report.to_dict(),
        }
    except Exception as e:
        logger.error(f"[API] validate_strategy error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
