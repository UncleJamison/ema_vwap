"""
Batch Optimizer Module — Multi-coin, Multi-timeframe Optuna Sweep.

Iterates over a configurable list of symbols × timeframes, runs Optuna
optimization on each combination, and persists results to the SQLite
batch_results leaderboard table.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.config import OptunaConfig, StrategyParams
from src.data_loader import DataLoader
from src.database import CandleDatabase
from src.optimizer import OptunaOptimizer

db = CandleDatabase()

# -------------------------------------------------------------------
# Shared progress state (read by the /api/batch_status endpoint)
# -------------------------------------------------------------------
_batch_lock = threading.Lock()
_batch_state: dict[str, Any] = {
    "running": False,
    "done": 0,
    "total": 0,
    "current_combo": "",
    "error": None,
}


def get_batch_state() -> dict[str, Any]:
    with _batch_lock:
        return dict(_batch_state)


def _update_state(**kwargs: Any) -> None:
    with _batch_lock:
        _batch_state.update(kwargs)


# -------------------------------------------------------------------
# Config dataclass
# -------------------------------------------------------------------
@dataclass
class BatchOptimizerConfig:
    symbols: list[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    timeframes: list[str] = field(default_factory=lambda: ["5m", "15m", "1h"])
    exchange: str = "kucoin"
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
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] = field(
        default_factory=lambda: ["sharpe_ratio", "max_drawdown_pct"]
    )
    enable_hard_drawdown_constraint: bool = False
    max_drawdown_constraint_pct: float = 25.0
    enable_max_holding_bars: bool = False
    max_holding_bars_min: int = 4
    max_holding_bars_max: int = 96
    trade_direction: str = "long_only"
    min_candles: int = 500  # skip combos with fewer bars than this

    # Pullback tolerance range (default to OptunaConfig defaults)
    pullback_tolerance_min: float = 0.05
    pullback_tolerance_max: float = 1.5


# -------------------------------------------------------------------
# Batch optimizer
# -------------------------------------------------------------------
class BatchOptimizer:
    """Orchestrates multi-symbol, multi-timeframe Optuna optimization runs."""

    def __init__(self, config: BatchOptimizerConfig | None = None) -> None:
        self.config = config or BatchOptimizerConfig()

    def run(self) -> list[dict[str, Any]]:
        """Run sweeps sequentially across all symbol x timeframe combos."""
        cfg = self.config
        combos = [(sym, tf) for sym in cfg.symbols for tf in cfg.timeframes]
        total = len(combos)

        _update_state(running=True, done=0, total=total, current_combo="", error=None)

        try:
            for idx, (symbol, timeframe) in enumerate(combos):
                combo_str = f"{symbol} ({timeframe})"
                _update_state(current_combo=combo_str, done=idx)
                print(f"[BatchOptimizer] ({idx + 1}/{total}) Optimizing {combo_str}...")
                self._run_single_optuna(symbol, timeframe)
            _update_state(running=False, done=total, current_combo="Complete")
        except Exception as exc:
            _update_state(running=False, error=str(exc))
            raise

        return db.load_batch_results(target_metric=cfg.target_metric)

    def _run_single_optuna(self, symbol: str, timeframe: str) -> None:
        """Optimize a single symbol/timeframe and persist the result."""
        cfg = self.config

        df = DataLoader.load_candles(
            exchange=cfg.exchange,
            symbol=symbol,
            timeframe=timeframe,
            days=cfg.days,
        )

        if df.empty or len(df) < cfg.min_candles:
            print(
                f"[BatchOptimizer] Skipping {symbol}/{timeframe}: "
                f"only {len(df)} candles (min {cfg.min_candles})"
            )
            return

        strategy_mode = cfg.strategy_mode
        base_params = StrategyParams(
            strategy_mode=strategy_mode,  # type: ignore
            trade_direction=cfg.trade_direction,  # type: ignore
        )

        # For OptunaConfig, target_metric must be a single supported metric literal
        # (used as reference/fallback), while multi_objective_metrics carries the Pareto set.
        optuna_target = cfg.target_metric
        if "|" in optuna_target:
            optuna_target = optuna_target.split("|")[0]
        if cfg.enable_multi_objective and cfg.multi_objective_metrics:
            optuna_target = cfg.multi_objective_metrics[0]

        opt_config = OptunaConfig(
            n_trials=cfg.n_trials,
            target_metric=optuna_target,
            min_trades=cfg.min_trades,
            seed_base_params=cfg.seed_base_params,
            multivariate=True,
            enable_multi_objective=cfg.enable_multi_objective,
            multi_objective_metrics=cfg.multi_objective_metrics,
            enable_hard_drawdown_constraint=cfg.enable_hard_drawdown_constraint,
            max_drawdown_constraint_pct=cfg.max_drawdown_constraint_pct,
            enable_max_holding_bars=cfg.enable_max_holding_bars,
            max_holding_bars_min=cfg.max_holding_bars_min,
            max_holding_bars_max=cfg.max_holding_bars_max,
            fast_ema_min=cfg.fast_ema_min,
            fast_ema_max=cfg.fast_ema_max,
            slow_ema_min=cfg.slow_ema_min,
            slow_ema_max=cfg.slow_ema_max,
            trend_ema_min=cfg.trend_ema_min,
            trend_ema_max=cfg.trend_ema_max,
            volume_multiplier_min=cfg.volume_multiplier_min,
            volume_multiplier_max=cfg.volume_multiplier_max,
            atr_multiplier_min=cfg.atr_multiplier_min,
            atr_multiplier_max=cfg.atr_multiplier_max,
            risk_reward_min=cfg.risk_reward_min,
            risk_reward_max=cfg.risk_reward_max,
            vwap_slope_min=0.00001,
            vwap_slope_max=cfg.vwap_slope_max_bound,
            pullback_tolerance_min=cfg.pullback_tolerance_min,
            pullback_tolerance_max=cfg.pullback_tolerance_max,
        )

        optimizer = OptunaOptimizer(base_params=base_params, config=opt_config)
        result = optimizer.optimize(df)

        if not result or result.get("best_params") is None:
            print(f"[BatchOptimizer] No result for {symbol}/{timeframe}; skipping.")
            return

        metrics: dict = result.get("best_backtest_result", {})
        trade_count = int(metrics.get("total_trades", 0))
        if trade_count < cfg.min_trades:
            print(
                f"[BatchOptimizer] Skipping {symbol}/{timeframe}: "
                f"only {trade_count} trades (min {cfg.min_trades})"
            )
            return

        best_value_value = result.get("best_value")
        if best_value_value is None and cfg.enable_multi_objective:
            best_value_value = float(
                result.get("pareto_frontier", [{}])[0]
                .get("objectives", {})
                .get(cfg.multi_objective_metrics[0], 0.0)
            )
        best_value = float(
            best_value_value if best_value_value is not None else float("-inf")
        )

        # Persist full multi-metric set if multi-objective optimization was run
        if cfg.enable_multi_objective and cfg.multi_objective_metrics:
            stored_metric = "|".join(cfg.multi_objective_metrics)
        else:
            stored_metric = cfg.target_metric

        db.save_batch_result(
            exchange=cfg.exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=result["best_params"].get("strategy_mode", strategy_mode),
            target_metric=stored_metric,
            best_value=best_value,
            trade_count=trade_count,
            sharpe_ratio=float(metrics.get("sharpe_ratio", 0.0)),
            calmar_ratio=float(metrics.get("calmar_ratio", 0.0)),
            win_rate=float(metrics.get("win_rate", 0.0)),
            max_drawdown_pct=float(metrics.get("max_drawdown_pct", 0.0)),
            total_return_pct=float(metrics.get("total_return_pct", 0.0)),
            best_params=json.dumps(result["best_params"]),
            run_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        print(
            f"[BatchOptimizer] Saved {symbol}/{timeframe}: "
            f"{cfg.target_metric}={best_value:.4f}, trades={trade_count}"
        )
