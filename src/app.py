"""
FastAPI Server and API Endpoints for EMA + VWAP Trading System.
Provides REST endpoints for candle fetching, indicator calculation, strategy backtesting,
background task management, system settings, and static file hosting for the Web Dashboard.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, ClassVar, Literal

import pandas as pd
from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.backtester import BacktestEngine
from src.batch_optimizer import BatchOptimizer, BatchOptimizerConfig, get_batch_state
from src.config import (
    MonteCarloConfig,
    OptunaConfig,
    StrategyParams,
    WFOConfig,
)
from src.data_loader import DataLoader
from src.database import CandleDatabase
from src.logger import logger, setup_logging
from src.monte_carlo import MonteCarloSimulator
from src.optimizer import OptunaOptimizer
from src.paper import PaperLedger, PaperProfileRegistry, PaperTradingEngine
from src.portfolio import portfolio_aggregator
from src.regime import (
    INSUFFICIENT_DATA,
    REGIME_MAP,
    get_current_regime,
)
from src.settings import SettingsManager
from src.strategy import EmaVwapStrategy
from src.tasks import global_task_manager
from src.validator import StrategyValidator, ValidationCriteria
from src.wfo import WalkForwardEngine
from src.ws_server import WebSocketManager

# Initialize Rotating File & Console Logging
setup_logging()
_batch_db = CandleDatabase()
_settings_mgr = SettingsManager(_batch_db)
_paper_profiles = PaperProfileRegistry(_batch_db)
_paper_ledger = PaperLedger(_batch_db)
_paper_engine = PaperTradingEngine(db=_batch_db)

# WebSocket manager for real-time dashboard
_ws_manager = WebSocketManager()


class _PollFilter(logging.Filter):
    """Suppress noisy GET /api/batch_status and /api/tasks 200 lines from the uvicorn access log."""

    _SUPPRESS: ClassVar[set[str]] = {"/api/batch_status", "/api/tasks"}

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg and "200" in msg for path in self._SUPPRESS)


# Install the filter once at import time (works with --reload too)
logging.getLogger("uvicorn.access").addFilter(_PollFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifespan manager for startup and graceful shutdown."""
    logger.info(
        "[Lifespan] Initializing database and evaluating paper runner auto-start..."
    )
    _batch_db.init_db()

    # Check if live paper polling was active prior to restart
    runner_enabled = _settings_mgr.get("paper_runner_enabled", "false")
    is_enabled = str(runner_enabled).lower() == "true"
    interval_str = _settings_mgr.get("paper_polling_interval", "15")
    try:
        interval = int(interval_str or 15)
    except ValueError:
        interval = 15

    if is_enabled:
        logger.info(
            f"[Lifespan] Auto-resuming live paper trading runner (interval={interval}s)..."
        )
        _paper_engine.start_polling(interval_seconds=interval, persist=False)
    else:
        logger.info("[Lifespan] Paper trading runner is in standby mode.")

    yield

    logger.info("[Lifespan] Shutting down: stopping paper polling runner...")
    _paper_engine.stop_polling(persist=False)


app = FastAPI(
    title="EMA + VWAP Crypto Trading Suite",
    description="Quantitative Crypto Trading System utilizing EMA timing and VWAP institutional bias.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(content=b"", status_code=204)


# Enable CORS for local web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class BacktestRequest(BaseStrategyRequest):
    pass


def build_strategy_params(req: BaseStrategyRequest) -> StrategyParams:
    """Build StrategyParams from request, automatically preserving all evaluated parameters."""
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return StrategyParams.from_dict(data)


class SyncHistoryRequest(BaseModel):
    exchange: str = "kucoin"
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    days: int = 180


@app.post("/api/sync_history")
def sync_history(req: SyncHistoryRequest) -> dict[str, Any]:
    try:
        logger.info(
            f"[API] Syncing {req.days} days of historical candles for {req.symbol} ({req.exchange})..."
        )
        df = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            days=req.days,
            limit=None,
            use_cache=False,
        )
        return {
            "status": "success",
            "message": f"Successfully synced {len(df)} candles for {req.symbol} ({req.exchange}, {req.days} days) into database.",
            "candle_count": len(df),
            "start_time": str(df["timestamp"].iloc[0]) if not df.empty else None,
            "end_time": str(df["timestamp"].iloc[-1]) if not df.empty else None,
        }
    except Exception as e:
        logger.error(f"[API] sync_history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/clear_cache")
def clear_cache() -> dict[str, Any]:
    deleted = _batch_db.clear_cache()
    return {
        "status": "success",
        "message": f"Successfully cleared {deleted} cached candles from database.",
    }


@app.get("/api/exchanges")
def get_exchanges() -> dict[str, Any]:
    return {
        "exchanges": [
            {
                "id": "gemini",
                "name": "Gemini (Live / Public API)",
                "default_symbol": "BTC/USD",
            },
            {
                "id": "kucoin",
                "name": "KuCoin (Live / Public API)",
                "default_symbol": "BTC-USDT",
            },
            {
                "id": "synthetic",
                "name": "Synthetic Generator (Offline Testing)",
                "default_symbol": "BTC/USD",
            },
        ],
        "timeframes": ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"],
        "strategy_modes": [
            {"id": "crossover", "name": "Setup 1: EMA 9 x VWAP Crossover"},
            {
                "id": "multi_ema",
                "name": "Setup 2: Multi-EMA Confirmation (8/21 x 50 VWAP)",
            },
            {"id": "pullback", "name": "Setup 3: VWAP Pullback Entry"},
        ],
    }


@app.post("/api/backtest")
def run_backtest(req: BacktestRequest) -> dict[str, Any]:
    try:
        # Load Candles from Database Cache or Exchange API
        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=req.limit,
            days=req.days,
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
        raise HTTPException(status_code=500, detail=str(e))


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


@app.post("/api/optimize")
def run_optimization(req: OptimizeRequest) -> dict[str, Any]:
    try:
        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=req.limit,
            days=req.days,
        )
        base_params = build_strategy_params(req)
        opt_config = OptunaConfig(
            target_metric=req.target_metric,  # type: ignore
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
        )
        optimizer = OptunaOptimizer(base_params=base_params, config=opt_config)
        res = optimizer.optimize(df_candles)
        return {
            "status": "success",
            "symbol": req.symbol,
            "exchange": req.exchange,
            "timeframe": req.timeframe,
            "results": res,
        }
    except Exception as e:
        logger.error(f"[API] run_optimization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/walk_forward")
def run_walk_forward(req: WFORequest) -> dict[str, Any]:
    try:
        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=req.limit,
            days=req.days,
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/monte_carlo")
def run_monte_carlo(req: MonteCarloRequest) -> dict[str, Any]:
    try:
        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=req.limit,
            days=req.days,
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
        raise HTTPException(status_code=500, detail=str(e))


class ValidateStrategyRequest(BaseModel):
    exchange: str = "kucoin"
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    limit: int = 500
    days: int | None = None
    params: dict[str, Any] = {}
    criteria: dict[str, Any] | None = None
    n_trials: int = 30  # Optuna trial budget for Gate 2 WFO (mirrors UI setting)
    target_metric: str = (
        "sharpe_ratio"  # Metric that was optimized; informs Gate 1 scoring
    )
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] | None = None


@app.post("/api/strategy/validate")
def validate_strategy_endpoint(req: ValidateStrategyRequest) -> dict[str, Any]:
    """
    Automated multi-stage robustness validation (Backtest + WFO + Monte Carlo)
    for a strategy configuration. Returns structured report and PASS/CAUTION/FAIL status.
    """
    try:
        df_candles = DataLoader.load_candles(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            limit=req.limit,
            days=req.days,
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
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Background Task & Progress Polling Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/tasks")
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
) -> dict[str, Any]:
    """List recent background tasks."""
    tasks = global_task_manager.list_tasks(limit=limit, status=status)
    return {"status": "success", "count": len(tasks), "tasks": tasks}


@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str) -> dict[str, Any]:
    """Poll specific background task progress and status."""
    task = global_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return {"status": "success", "task": task}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a running background task."""
    success = global_task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Task '{task_id}' could not be cancelled (already finished or not found).",
        )
    return {
        "status": "success",
        "task_id": task_id,
        "message": "Task cancelled successfully.",
    }


# ---------------------------------------------------------------------------
# Settings & Credentials Store Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    """Retrieve all system settings and credentials with secrets safely masked."""
    settings = _settings_mgr.get_all_masked()
    return {"status": "success", "settings": settings}


@app.post("/api/settings")
def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Save or update system settings and API credentials."""
    if not payload:
        raise HTTPException(status_code=400, detail="Empty settings payload provided.")
    updated = _settings_mgr.update_bulk(payload)
    return {
        "status": "success",
        "message": f"Successfully updated {updated} setting(s).",
    }


@app.get("/api/regime/current")
def get_regime_current() -> dict[str, Any]:
    """Return the current detected regime (trending / ranging / high_vol).

    Computes the regime live from the latest available candles for the default
    symbol/timeframe via classify_regime(), falling back to INSUFFICIENT_DATA
    if no data is available.
    """

    try:
        df = DataLoader.load_candles(
            exchange="kucoin",
            symbol="BTC/USD",
            timeframe="1h",
            days=2,
        )
        if df.empty or len(df) < 60:
            regime = INSUFFICIENT_DATA
        else:
            regime = get_current_regime(df)
    except Exception:
        regime = INSUFFICIENT_DATA

    return {
        "status": "success",
        "regime": regime,
        "last_computed": datetime.now(timezone.utc).isoformat(),
        "regime_map": dict(REGIME_MAP),
    }

# ---------------------------------------------------------------------------
# Batch Sweep Endpoints
# ---------------------------------------------------------------------------


class BatchOptimizeRequest(BaseModel):
    exchange: str = "kucoin"
    symbols: list[str] = ["BTC/USD", "ETH/USD", "SOL/USD"]
    timeframes: list[str] = ["5m", "15m", "1h"]
    days: int = 180
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


class BatchResultFavoriteRequest(BaseModel):
    favorite: bool


def _run_batch_task(cfg: BatchOptimizerConfig) -> None:
    """Background thread target — runs the full sweep without blocking the HTTP response."""
    try:
        BatchOptimizer(cfg).run()
    except Exception as exc:
        from src.batch_optimizer import _update_state

        _update_state(running=False, error=str(exc))


@app.post("/api/batch_optimize")
def start_batch_optimize(
    req: BatchOptimizeRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Start a multi-coin/multi-timeframe Optuna sweep in the background."""
    state = get_batch_state()
    if state["running"]:
        raise HTTPException(status_code=409, detail="A batch sweep is already running.")
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
    background_tasks.add_task(_run_batch_task, cfg)
    total = len(req.symbols) * len(req.timeframes)
    return {"status": "started", "total_combos": total}


@app.get("/api/batch_status")
def get_batch_status() -> dict[str, Any]:
    """Poll the current batch sweep progress."""
    return get_batch_state()


@app.get("/api/batch_results")
def get_batch_results(
    target_metric: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Return the leaderboard from the batch_results table, sorted by best_value."""
    rows = _batch_db.load_batch_results(target_metric=target_metric, limit=limit)
    return {"status": "success", "count": len(rows), "results": rows}


@app.delete("/api/batch_results")
def delete_batch_results(req: BatchResultsDeleteRequest) -> dict[str, Any]:
    """Delete selected batch sweep results by database ID."""
    if not req.ids or any(result_id <= 0 for result_id in req.ids):
        raise HTTPException(
            status_code=400, detail="Provide one or more valid result IDs."
        )
    deleted = _batch_db.delete_batch_results(req.ids)
    return {"status": "success", "deleted": deleted}


class BatchResultValidationRequest(BaseModel):
    status: str
    report_json: str


@app.patch("/api/batch_results/{result_id}/favorite")
def set_batch_result_favorite(
    result_id: int, req: BatchResultFavoriteRequest
) -> dict[str, Any]:
    """Set the saved/favorite state for one batch sweep result."""
    if result_id <= 0:
        raise HTTPException(status_code=400, detail="Provide a valid result ID.")
    updated = _batch_db.set_batch_result_favorite(result_id, req.favorite)
    if not updated:
        raise HTTPException(status_code=404, detail="Batch result not found.")
    return {"status": "success", "id": result_id, "favorite": req.favorite}


@app.post("/api/batch_results/auto-validate")
def auto_validate_batch_results(
    req: BatchResultsAutoValidateRequest,
) -> dict[str, Any]:
    """Auto-validate all saved batch results (or a filtered subset) after a sweep.

    Runs the 3-gate robustness check on each result and persists the
    validation_status / validation_json back to the batch_results table.
    Designed to be triggered by the Batch Sweep tab's Auto-Validate checkbox.
    """
    from src.validator import StrategyValidator

    try:
        # Load results to validate (optionally filtered by target_metric)
        rows = _batch_db.load_batch_results(
            target_metric=req.target_metric, limit=1000
        )
        if not rows:
            return {"status": "success", "validated_count": 0, "message": "No results to validate."}

        validated = 0
        errors: list[str] = []
        for row in rows:
            try:
                params_dict = json.loads(row.get("best_params") or "{}")
                exchange = row.get("exchange", req.exchange or "kucoin")
                symbol = row.get("symbol", "BTC/USD")
                timeframe = row.get("timeframe", "5m")
                target_metric = row.get("target_metric") or req.target_metric or "sharpe_ratio"
                is_multi = bool(target_metric and "|" in target_metric)
                multi_metrics = (
                    target_metric.split("|").filter(bool) if is_multi else None
                )
                resolved_target = multi_metrics[0] if is_multi else target_metric

                # Load candles for validation
                df_candles = DataLoader.load_candles(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=500,
                )
                strat_params = StrategyParams.from_dict(params_dict) if params_dict else StrategyParams()
                validator = StrategyValidator()
                report = validator.validate(
                    df_candles=df_candles,
                    params=strat_params,
                    target_metric=resolved_target,
                    enable_multi_objective=is_multi,
                    multi_objective_metrics=multi_metrics,
                )
                _batch_db.update_batch_result_validation(
                    result_id=row["id"],
                    status=report.status,
                    report_json=json.dumps(report.to_dict()),
                )
                validated += 1
            except Exception as exc:
                errors.append(f"{row.get('symbol', '?')}/{row.get('timeframe', '?')}: {exc}")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/batch_results/{result_id}/validation")
def update_batch_result_validation(
    result_id: int, req: BatchResultValidationRequest
) -> dict[str, Any]:
    """Save validation status and report JSON for a batch result row."""
    if result_id <= 0:
        raise HTTPException(status_code=400, detail="Provide a valid result ID.")
    updated = _batch_db.update_batch_result_validation(
        result_id=result_id, status=req.status, report_json=req.report_json
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Batch result not found.")
    return {"status": "success", "id": result_id, "validation_status": req.status}


@app.post("/api/batch_results/purge")
def purge_batch_results(req: BatchResultsPurgeRequest) -> dict[str, Any]:
    """Purge stale batch sweep results by age, optionally limited to one metric."""
    if req.older_than_days < 1:
        raise HTTPException(
            status_code=400, detail="older_than_days must be at least 1."
        )
    deleted = _batch_db.purge_batch_results(
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


@app.post("/api/batch_results/export_to_portfolio")
def export_batch_to_portfolio(req: BatchToPortfolioRequest) -> dict[str, Any]:
    """Export selected batch sweep results to portfolio rebalancer with computed target weights."""
    try:
        # Load the selected batch results
        all_rows = _batch_db.load_batch_results(limit=1000)
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
        raise HTTPException(status_code=500, detail=str(e))

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
            enable_multi_objective: bool = False
            multi_objective_metrics: list[str] | None = None

        @app.post("/api/tear-sheet/pdf")
        async def generate_tear_sheet_pdf(req: TearSheetRequest) -> Response:
            """Generate and return a PDF tear sheet for the given strategy configuration."""
            try:
                from src.web.pdf_renderer import (
                    generate_tear_sheet_pdf,
                )

                # Load candles for backtest and validation
                df_candles = DataLoader.load_candles(
                    exchange=req.exchange,
                    symbol=req.symbol,
                    timeframe=req.timeframe,
                    limit=1000,
                    days=180,
                )

                # If no validation report provided, run validation
                if req.validation_report is None:
                    from src.config import MonteCarloConfig, StrategyParams
                    from src.validator import StrategyValidator

                    strat_params = StrategyParams.from_dict(req.params)
                    validator = StrategyValidator()
                    report = validator.validate(
                        df_candles=df_candles,
                        params=strat_params,
                        n_trials=30,
                        target_metric=req.target_metric,
                        enable_multi_objective=req.enable_multi_objective,
                        multi_objective_metrics=req.multi_objective_metrics,
                    )
                    validation_report = report.to_dict()
                else:
                    validation_report = req.validation_report
                    strat_params = StrategyParams.from_dict(req.params)

                # Run Monte Carlo for equity curves and percentiles
                from src.monte_carlo import MonteCarloConfig, MonteCarloSimulator

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
                    "win_rate_pct": (
                        round(len(wins) / len(pnls) * 100, 1) if pnls else 0
                    ),
                    "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                    "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                    "largest_win": round(max(wins), 2) if wins else 0,
                    "largest_loss": round(min(losses), 2) if losses else 0,
                    "profit_factor": (
                        round(sum(wins) / abs(sum(losses)), 2)
                        if losses
                        else float("inf")
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

                filename = f"ema_vwap_tear_sheet_{req.symbol.replace('/', '_')}_{req.timeframe}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.…"
                return Response(
                    content=pdf_bytes,
                    media_type="application/pdf",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"'
                    },
                )

            except Exception as e:
                logger.error(f"[API] generate_tear_sheet_pdf error: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/tear-sheet/html")
        async def generate_tear_sheet_html(req: TearSheetRequest) -> Response:
            """Generate and return an interactive HTML tear sheet for the given strategy configuration."""
            try:
                from src.web.pdf_renderer import (
                    generate_tear_sheet_html,
                )

                # Load candles for backtest and validation
                df_candles = DataLoader.load_candles(
                    exchange=req.exchange,
                    symbol=req.symbol,
                    timeframe=req.timeframe,
                    limit=1000,
                    days=180,
                )

                # If no validation report provided, run validation
                if req.validation_report is None:
                    from src.config import StrategyParams
                    from src.validator import StrategyValidator

                    strat_params = StrategyParams.from_dict(req.params)
                    validator = StrategyValidator()
                    report = validator.validate(
                        df_candles=df_candles,
                        params=strat_params,
                        n_trials=30,
                        target_metric=req.target_metric,
                        enable_multi_objective=req.enable_multi_objective,
                        multi_objective_metrics=req.multi_objective_metrics,
                    )
                    validation_report = report.to_dict()
                else:
                    validation_report = req.validation_report
                    strat_params = StrategyParams.from_dict(req.params)

                # Run Monte Carlo for equity curves and percentiles
                from src.monte_carlo import MonteCarloConfig, MonteCarloSimulator

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
                    "win_rate_pct": (
                        round(len(wins) / len(pnls) * 100, 1) if pnls else 0
                    ),
                    "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                    "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                    "largest_win": round(max(wins), 2) if wins else 0,
                    "largest_loss": round(min(losses), 2) if losses else 0,
                    "profit_factor": (
                        round(sum(wins) / abs(sum(losses)), 2)
                        if losses
                        else float("inf")
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
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/tear-sheet/json")
        async def generate_tear_sheet_json(req: TearSheetRequest) -> dict[str, Any]:
            """Generate and return a JSON tear sheet bundle for the given strategy configuration."""
            try:

                # Load candles for backtest and validation
                df_candles = DataLoader.load_candles(
                    exchange=req.exchange,
                    symbol=req.symbol,
                    timeframe=req.timeframe,
                    limit=1000,
                    days=180,
                )

                # If no validation report provided, run validation
                if req.validation_report is None:
                    from src.config import StrategyParams
                    from src.validator import StrategyValidator

                    strat_params = StrategyParams.from_dict(req.params)
                    validator = StrategyValidator()
                    report = validator.validate(
                        df_candles=df_candles,
                        params=strat_params,
                        n_trials=30,
                        target_metric=req.target_metric,
                        enable_multi_objective=req.enable_multi_objective,
                        multi_objective_metrics=req.multi_objective_metrics,
                    )
                    validation_report = report.to_dict()
                else:
                    validation_report = req.validation_report
                    strat_params = StrategyParams.from_dict(req.params)

                # Run Monte Carlo for equity curves and percentiles
                from src.monte_carlo import MonteCarloConfig, MonteCarloSimulator

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
                    "win_rate_pct": (
                        round(len(wins) / len(pnls) * 100, 1) if pnls else 0
                    ),
                    "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
                    "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
                    "largest_win": round(max(wins), 2) if wins else 0,
                    "largest_loss": round(min(losses), 2) if losses else 0,
                    "profit_factor": (
                        round(sum(wins) / abs(sum(losses)), 2)
                        if losses
                        else float("inf")
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
                    "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
                    "report": validation_report,
                    "strategy_params": req.params,
                    "trade_distribution": trade_distribution,
                    "equity_curves": equity_curves,
                    "monte_carlo_percentiles": mc_percentiles,
                }

            except Exception as e:
                logger.error(f"[API] generate_tear_sheet_json error: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # -------------------------------------------------------------------------
        # Paper Trading Profiles Endpoints


# -------------------------------------------------------------------------


class DeployPaperProfileRequest(BaseModel):
    exchange: str = "kucoin"
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    strategy_mode: str = "crossover"
    target_metric: str = "sharpe_ratio"
    params: dict[str, Any]
    optuna_score: float = 0.0
    is_active: bool = True


@app.post("/api/paper/profiles/deploy")
def deploy_paper_profile(req: DeployPaperProfileRequest) -> dict[str, Any]:
    """1-Click deployment: Save & activate an Optuna trial parameter set for a symbol."""
    try:
        profile_id = _paper_profiles.save_profile(
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            strategy_mode=req.strategy_mode,
            target_metric=req.target_metric,
            params=req.params,
            optuna_score=req.optuna_score,
            is_active=req.is_active,
        )
        saved = _paper_profiles.get_profile(
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
            },
        }
    except Exception as e:
        logger.error(f"[API] deploy_paper_profile error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/paper/profiles")
def list_paper_profiles(
    exchange: str | None = None, active_only: bool = False
) -> dict[str, Any]:
    """List all saved per-asset paper trading profiles."""
    try:
        profiles = _paper_profiles.list_profiles(
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/paper/profiles/{exchange}/{symbol:path}")
def get_paper_profile_for_symbol(
    exchange: str,
    symbol: str,
    timeframe: str | None = None,
    strategy_mode: str | None = None,
) -> dict[str, Any]:
    """Get the active paper profile for a specific exchange and symbol."""
    try:
        profile = _paper_profiles.get_profile(
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
        raise HTTPException(status_code=500, detail=str(e))


class ToggleActiveRequest(BaseModel):
    is_active: bool


@app.post("/api/paper/profiles/{profile_id}/toggle-active")
def toggle_paper_profile_active(
    profile_id: int, req: ToggleActiveRequest
) -> dict[str, Any]:
    """Toggle the active state of a paper profile."""
    updated = _paper_profiles.set_active(profile_id, req.is_active)
    if not updated:
        raise HTTPException(status_code=404, detail="Paper profile not found.")
    return {"status": "success", "id": profile_id, "is_active": req.is_active}


@app.delete("/api/paper/profiles/{profile_id}")
def delete_paper_profile(profile_id: int) -> dict[str, Any]:
    """Delete a paper profile by ID."""
    deleted = _paper_profiles.delete_profile(profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Paper profile not found.")
    return {"status": "success", "deleted": True, "id": profile_id}


class UpdateProfileTradeDirectionRequest(BaseModel):
    trade_direction: Literal["long_only", "long_short", "short_only"]


@app.patch("/api/paper/profiles/{profile_id}/trade-direction")
def update_profile_trade_direction(
    profile_id: int, req: UpdateProfileTradeDirectionRequest
) -> dict[str, Any]:
    """Update the trade direction / account mode for a deployed paper profile."""
    updated = _paper_profiles.update_trade_direction(profile_id, req.trade_direction)
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


# -------------------------------------------------------------------------
# Paper Trading Execution Engine & Portfolio Endpoints
# -------------------------------------------------------------------------


class PaperStepRequest(BaseModel):
    exchange: str | None = None
    symbol: str | None = None
    timeframe: str = "5m"


@app.post("/api/paper/engine/step")
def step_paper_engine(req: PaperStepRequest) -> dict[str, Any]:
    """Execute a single evaluation step on a specific symbol or all active paper profiles."""
    try:
        if req.exchange and req.symbol:
            res = _paper_engine.evaluate_symbol(
                exchange=req.exchange,
                symbol=req.symbol,
                timeframe=req.timeframe,
                fetch_live=True,
            )
            return {"status": "success", "results": [res]}
        else:
            results = _paper_engine.step_all_active_profiles()
            return {"status": "success", "results": results}
    except Exception as e:
        logger.error(f"[API] step_paper_engine error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class PaperPollingRequest(BaseModel):
    interval_seconds: int = 15


@app.post("/api/paper/engine/start")
def start_paper_polling(req: PaperPollingRequest) -> dict[str, Any]:
    """Start background live polling runner for paper trading."""
    started = _paper_engine.start_polling(interval_seconds=req.interval_seconds)
    return {
        "status": "success" if started else "already_running",
        "is_running": _paper_engine.is_running,
        "interval_seconds": req.interval_seconds,
    }


@app.post("/api/paper/engine/stop")
def stop_paper_polling() -> dict[str, Any]:
    """Stop background live polling runner for paper trading."""
    stopped = _paper_engine.stop_polling()
    return {
        "status": "success" if stopped else "not_running",
        "is_running": _paper_engine.is_running,
    }


@app.post("/api/paper/engine/reconcile")
def reconcile_paper_engine() -> dict[str, Any]:
    """Audit open positions against missed historical candles and backfill SL/TP exits."""
    try:
        results = _paper_engine.reconcile_positions_on_startup()
        return {
            "status": "success",
            "reconciled_count": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error(f"[API] reconcile_paper_engine error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/paper/engine/status")
def get_paper_engine_status() -> dict[str, Any]:
    """Get live execution engine runner status, positions, balance, and stats."""
    return _paper_engine.get_status()


@app.get("/api/paper/positions")
def list_paper_positions(exchange: str | None = None) -> dict[str, Any]:
    """List all currently active open paper trading positions."""
    try:
        positions = _paper_ledger.list_positions(exchange=exchange)
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/paper/positions/{exchange}/{symbol:path}/close")
def close_paper_position_manual(exchange: str, symbol: str) -> dict[str, Any]:
    """Manually close an open paper position at current market price."""
    try:
        trade = _paper_engine.close_position_market(exchange=exchange, symbol=symbol)
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
        raise HTTPException(status_code=500, detail=str(e))


class UpdateSLTPRequest(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


@app.post("/api/paper/positions/{exchange}/{symbol:path}/sl_tp")
def update_paper_position_sl_tp(
    exchange: str, symbol: str, req: UpdateSLTPRequest
) -> dict[str, Any]:
    """Dynamically update Stop-Loss and/or Take-Profit levels on an active open paper position."""
    try:
        updated = _paper_engine.update_position_sl_tp(
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
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"[API] update_paper_position_sl_tp error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/paper/ledger/transactions")
def list_paper_ledger_transactions(
    account_id: str = "default",
    exchange: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List double-entry paper ledger transaction audit logs."""
    try:
        txs = _paper_ledger.list_transactions(
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/paper/trades/history")
def list_paper_trade_history(
    exchange: str | None = None,
    symbol: str | None = None,
    exit_reason: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List completed round-trip paper trade history logs."""
    try:
        trades = _paper_ledger.list_trade_history(
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/paper/performance")
def get_paper_performance(
    exchange: str | None = None, symbol: str | None = None
) -> dict[str, Any]:
    """Get aggregate paper trading performance metrics and equity stats."""
    try:
        stats = _paper_ledger.get_statistics(exchange=exchange, symbol=symbol)
        balance = _paper_ledger.get_balance()
        positions = _paper_ledger.list_positions(exchange=exchange)
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
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# Unified Multi-Asset Portfolio Management Endpoints
# -------------------------------------------------------------------------


@app.get("/api/portfolio/snapshot")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/portfolio/venues")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/portfolio/positions")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/portfolio/refresh")
def refresh_portfolio_balances(
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/portfolio/history")
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
        raise HTTPException(status_code=500, detail=str(e))


class PositionSizingRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_loss_price: float
    risk_per_trade_pct: float = 1.0
    allow_fractional: bool = True
    include_synthetic: bool = False


@app.get("/api/portfolio/correlation")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/portfolio/var")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/portfolio/size_position")
def size_portfolio_position(
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
        raise HTTPException(status_code=500, detail=str(e))


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


@app.post("/api/portfolio/route_order")
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/portfolio/rebalance/plan")
def create_portfolio_rebalance_plan(req: RebalancePlanRequest) -> dict[str, Any]:
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/portfolio/rebalance/execute")
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
        raise HTTPException(status_code=500, detail=str(e))


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


@app.post("/api/portfolio/risk_budget")
def compute_portfolio_risk_budget(
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
        raise HTTPException(status_code=500, detail=str(e))

        # WebSocket endpoint for real-time dashboard
        @app.websocket("/ws/dashboard")
        async def dashboard_websocket(websocket: WebSocket):
            """WebSocket endpoint for real-time dashboard updates."""
            await _ws_manager.connect(websocket)
            try:
                # Send initial status
                status = _paper_engine.get_status()
                await _ws_manager.send_personal(
                    websocket,
                    {
                        "type": "init",
                        "data": status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # Keep connection alive, handle incoming messages
                while True:
                    msg = await websocket.receive_text()
                    try:
                        data = json.loads(msg)
                        msg_type = data.get("type")

                        if msg_type == "ping":
                            await _ws_manager.send_personal(
                                websocket,
                                {
                                    "type": "pong",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                        elif msg_type == "get_status":
                            status = _paper_engine.get_status()
                            await _ws_manager.send_personal(
                                websocket,
                                {
                                    "type": "status_update",
                                    "data": status,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                        elif msg_type == "get_positions":
                            positions = _paper_engine.ledger.list_positions()
                            await _ws_manager.send_personal(
                                websocket,
                                {
                                    "type": "positions_update",
                                    "data": [p.to_dict() for p in positions],
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                        elif msg_type == "step_engine":
                            # Trigger a single engine step
                            results = _paper_engine.step_all_active_profiles()
                            await _ws_manager.send_personal(
                                websocket,
                                {
                                    "type": "engine_step_result",
                                    "data": results,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                            # Broadcast updated status to all
                            status = _paper_engine.get_status()
                            await _ws_manager.broadcast(
                                {
                                    "type": "status_update",
                                    "data": status,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )

                    except Exception as e:
                        logger.error(f"[WebSocket] Message handling error: {e}")

            except WebSocketDisconnect:
                await _ws_manager.disconnect(websocket)
            except Exception as e:
                logger.error(f"[WebSocket] Connection error: {e}")
                await _ws_manager.disconnect(websocket)

        # Mount Web Static Files


web_dir = os.path.join(os.path.dirname(__file__), "web")
if os.path.exists(web_dir):
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    def read_root():
        index_file = os.path.join(web_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return JSONResponse(
            {"message": "EMA + VWAP Trading System API running. Static UI loading..."}
        )


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    reload_flag = os.environ.get("RELOAD", "false").lower() == "true"
    uvicorn.run("src.app:app", host=host, port=port, reload=reload_flag)
