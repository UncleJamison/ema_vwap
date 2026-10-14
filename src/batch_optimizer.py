"""
Batch Optimizer Module — Multi-coin, Multi-timeframe Optuna Sweep.

Iterates over a configurable list of symbols × timeframes, runs Optuna
optimization on each combination, and persists results to the SQLite
batch_results leaderboard table.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src.config import (
    MAX_OPTUNA_WORKERS,
    OptunaConfig,
    StrategyParams,
    get_ema_bounds,
    get_optimal_lookback_days,
)
from src.data_loader import DataLoader, get_donor_timeframe
from src.db_connection_manager import get_database
from src.indicators import compute_slope_bounds_from_df
from src.logger import logger
from src.optimizer import OptunaOptimizer
from src.tasks import TaskCancelledException
from src.validator_symbols import validate_symbol_format

db = get_database("batch_results")

# -------------------------------------------------------------------
# Shared progress state (read by the /api/batch_status endpoint)
# -------------------------------------------------------------------
_batch_lock = threading.Lock()
_batch_state: dict[str, Any] = {
    "running": False,
    "done": 0,
    "total": 0,
    "current_combo": "",
    "combo_progress": 0.0,
    "error": None,
    "cancelled": False,
}


def get_batch_state() -> dict[str, Any]:
    with _batch_lock:
        return dict(_batch_state)


def _update_state(**kwargs: Any) -> None:
    with _batch_lock:
        _batch_state.update(kwargs)


def cancel_batch() -> bool:
    """Request cooperative cancellation of any running batch sweep."""
    with _batch_lock:
        if _batch_state.get("running"):
            _batch_state["cancelled"] = True
            return True
        return False


def reset_batch_state() -> None:
    """Reset the batch state dictionary (primarily for test isolation)."""
    with _batch_lock:
        _batch_state.clear()
        _batch_state.update(
            {
                "running": False,
                "done": 0,
                "total": 0,
                "current_combo": "",
                "combo_progress": 0.0,
                "error": None,
                "cancelled": False,
            }
        )


# -------------------------------------------------------------------
# Config dataclass
# -------------------------------------------------------------------
@dataclass
class BatchOptimizerConfig:
    symbols: list[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    timeframes: list[str] = field(default_factory=lambda: ["5m", "15m", "1h"])
    exchange: str = "kucoin"
    days: int | None = None  # if None, uses lookback_days from config.py JDR
    n_trials: int = 100
    target_metric: str = "sharpe_ratio"
    strategy_mode: str = "auto"
    min_trades: int = 5
    execution_mode: str = "sequential"  # "sequential" | "multiprocess"
    n_workers: int = 1

    # Search ranges (default to OptunaConfig defaults)
    fast_ema_min: int = 3  # wider global; per-timeframe override via get_ema_bounds
    fast_ema_max: int = 30
    slow_ema_min: int = 10
    slow_ema_max: int = 100
    trend_ema_min: int = 50
    trend_ema_max: int = 250
    volume_multiplier_min: float = 0.8
    volume_multiplier_max: float = 3.5
    atr_multiplier_min: float = 2.0
    atr_multiplier_max: float = 5.0
    risk_reward_min: float = 1.0
    risk_reward_max: float = 6.0
    vwap_slope_max_bound: float = 0.003
    n_jobs: int = 1
    n_startup_trials: int | None = None
    sampler_seed: int | None = 42
    seed_base_params: bool = True
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] = field(
        default_factory=lambda: ["sharpe_ratio", "max_drawdown_pct"]
    )
    enable_hard_drawdown_constraint: bool = False
    max_drawdown_constraint_pct: float = 25.0
    enable_max_holding_bars: bool = False
    max_holding_bars_min: int = 4
    max_holding_bars_max: int = 500
    trade_direction: str = "long_only"
    min_candles: int = 500  # skip combos with fewer bars than this

    # Pullback tolerance range (default to OptunaConfig defaults)
    pullback_tolerance_min: float = 0.05
    pullback_tolerance_max: float = 1.5

    def __post_init__(self) -> None:
        if self.execution_mode not in {"sequential", "multiprocess"}:
            raise ValueError(
                f"Invalid execution_mode '{self.execution_mode}'. Must be 'sequential' or 'multiprocess'."
            )
        if self.execution_mode == "sequential":
            self.n_workers = 1
        else:
            self.n_workers = min(max(1, int(self.n_workers)), MAX_OPTUNA_WORKERS)


ALL_SINGLE_METRICS: list[str] = [
    "sharpe_ratio",
    "drawdown_penalized_sharpe",
    "calmar_ratio",
    "sortino_ratio",
    "drawdown_penalized_sortino",
    "ulcer_index",
    "profit_factor",
    "net_profit",
    "total_return_pct",
    "win_rate",
]

ALL_CURATED_MULTI_PAIRS: list[list[str]] = [
    ["sharpe_ratio", "max_drawdown_pct"],
    ["sharpe_ratio", "total_return_pct"],
    ["sharpe_ratio", "win_rate"],
    ["sortino_ratio", "max_drawdown_pct"],
    ["calmar_ratio", "max_drawdown_pct"],
    ["drawdown_penalized_sharpe", "max_drawdown_pct"],
    ["drawdown_penalized_sortino", "max_drawdown_pct"],
    ["profit_factor", "total_return_pct"],
    ["profit_factor", "max_drawdown_pct"],
    ["net_profit", "max_drawdown_pct"],
    ["total_return_pct", "win_rate"],
    ["ulcer_index", "total_return_pct"],
]


# -------------------------------------------------------------------
# Batch optimizer
# -------------------------------------------------------------------
class BatchOptimizer:
    """Orchestrates multi-symbol, multi-timeframe Optuna optimization runs."""

    def __init__(self, config: BatchOptimizerConfig | None = None) -> None:
        self.config = config
        self._base_candles_cache: dict[str, pd.DataFrame] = {}

    def _get_base_candles(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        """Return base (finest) timeframe candles for a symbol, fetching once.

        For multi-timeframe sweeps on the same symbol, we fetch each timeframe
        natively when supported, otherwise resample from the finest native timeframe.
        When resampling, we fetch the base timeframe with enough days to cover
        the target timeframe's optimal lookback.
        """
        exchange = self.config.exchange
        exchange_lower = exchange.lower()

        # Check if the target timeframe is natively supported
        donor = get_donor_timeframe(exchange_lower, timeframe)

        if donor is None:
            # Timeframe is natively supported - fetch it directly with its optimal lookback
            days = self.config.days
            if days is None:
                days = get_optimal_lookback_days(timeframe, "crypto")

            df = DataLoader.load_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                days=days,
            )
            return df

        # Timeframe needs resampling - use the donor timeframe as base
        # But fetch enough days to cover the TARGET timeframe's optimal lookback
        base_tf = donor  # use the actual donor timeframe

        cache_key = f"{symbol}:{base_tf}:{timeframe}"  # include target tf for different lookbacks
        if cache_key in self._base_candles_cache:
            return self._base_candles_cache[cache_key]

        # Fetch base timeframe with TARGET timeframe's optimal lookback days
        days = self.config.days
        if days is None:
            days = get_optimal_lookback_days(timeframe, "crypto")  # target's lookback

        df = DataLoader.load_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=base_tf,
            days=days,
        )
        self._base_candles_cache[cache_key] = df
        return df

    def _resample_from_base(
        self, base_df: pd.DataFrame, target_tf: str
    ) -> pd.DataFrame:
        """Resample base timeframe DataFrame to target timeframe."""
        if base_df.empty:
            return pd.DataFrame()

        donor_tf = get_donor_timeframe(self.config.exchange.lower(), target_tf)
        if donor_tf:
            return DataLoader._resample_df(base_df, donor_tf, target_tf)
        return base_df

    def run(
        self,
        is_cancelled: Callable[[], bool] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        reset_state: bool = True,
        done_offset: int = 0,
        total_override: int | None = None,
        combo_label_prefix: str = "",
        mark_complete: bool = True,
    ) -> list[dict[str, Any]]:
        """Run sweeps sequentially across all symbol x timeframe combos."""

        cfg = self.config
        combos = [(sym, tf) for sym in cfg.symbols for tf in cfg.timeframes]
        combos_count = len(combos)
        total = total_override if total_override is not None else combos_count

        if reset_state:
            _update_state(
                running=True,
                done=done_offset,
                total=total,
                current_combo="",
                combo_progress=0.0,
                error=None,
                cancelled=False,
            )
        else:
            _update_state(
                running=True,
                done=done_offset,
                total=total,
            )

        def _is_cancelled() -> bool:
            if is_cancelled is not None and is_cancelled():
                return True
            return bool(get_batch_state().get("cancelled", False))

        try:
            for idx, (symbol, timeframe) in enumerate(combos):
                if _is_cancelled():
                    _update_state(running=False, error="Batch sweep cancelled")
                    print(
                        "[BatchOptimizer] Sweep cancelled before starting next combo."
                    )
                    break

                overall_done = done_offset + idx
                combo_str = f"{combo_label_prefix}{symbol} ({timeframe})"
                _update_state(
                    current_combo=combo_str, done=overall_done, combo_progress=0.0
                )
                print(
                    f"[BatchOptimizer] ({overall_done + 1}/{total}) Optimizing {combo_str}..."
                )

                def _combo_cb(
                    p: float,
                    desc: str,
                    _label: str = combo_str,
                    _idx: int = overall_done,
                ) -> None:
                    _update_state(combo_progress=round(p, 3))
                    if progress_callback is not None:
                        try:
                            progress_callback(
                                {
                                    "combo": _label,
                                    "combo_index": _idx,
                                    "total_combos": total,
                                    "combo_progress": round(p, 3),
                                    "description": desc,
                                }
                            )
                        except Exception as e:
                            logger.warning(
                                f"[BatchOptimizer] Progress callback failed: {e}"
                            )

                self._run_single_optuna(
                    symbol,
                    timeframe,
                    is_cancelled=_is_cancelled,
                    progress_callback=_combo_cb,
                )
                _update_state(combo_progress=1.0, done=overall_done + 1)

            if _is_cancelled():
                _update_state(running=False, error="Batch sweep cancelled")
            elif mark_complete:
                _update_state(
                    running=False,
                    done=total,
                    current_combo="Complete",
                    combo_progress=1.0,
                )
        except TaskCancelledException:
            _update_state(running=False, error="Batch sweep cancelled")
            print("[BatchOptimizer] Sweep cancelled during combo optimization.")
        except Exception as exc:
            _update_state(running=False, error=str(exc))
            raise

        return db.load_batch_results(target_metric=cfg.target_metric)

    def _run_single_optuna(
        self,
        symbol: str,
        timeframe: str,
        is_cancelled: Callable[[], bool] | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> None:
        """Optimize a single symbol/timeframe and persist the result."""
        cfg = self.config

        symbol = validate_symbol_format(symbol)

        # Use pre-fetched base candles for multi-timeframe sweeps (ema_vwap-9jfi)
        base_df = self._get_base_candles(symbol, timeframe)
        if base_df is not None and not base_df.empty:
            # Check if we already have the target timeframe (native fetch) or need resampling
            donor = get_donor_timeframe(cfg.exchange.lower(), timeframe)
            if donor is None:
                # Native fetch - data is already in target timeframe
                df = base_df
            else:
                # Resample from base timeframe to target timeframe
                df = self._resample_from_base(base_df, timeframe)
        else:
            # Fallback to direct load (single timeframe or unsupported exchange)
            days = cfg.days
            if days is None:
                days = get_optimal_lookback_days(timeframe, "crypto")

            df = DataLoader.load_candles(
                exchange=cfg.exchange,
                symbol=symbol,
                timeframe=timeframe,
                days=days,
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

        # Resolve timeframe-aware EMA and VWAP-slope bounds (wider global defaults;
        # tighter per-TF when the combo selects a specific timeframe).

        _tf_for_bounds = timeframe
        _ema_bounds = get_ema_bounds(_tf_for_bounds, "crypto")

        # Auto-calibrate VWAP slope bounds from the actual candle data for this
        # specific symbol+timeframe.  This replaces the static per-TF table for
        # any symbol — new coins, illiquid alts, stablecoins — without needing
        # to manually re-run calibrate_bounds.py.  Falls back to the static table
        # when df is too short (< 100 valid slope samples).
        _slope_bounds = compute_slope_bounds_from_df(df, timeframe=_tf_for_bounds)
        logger.debug(
            f"[BatchOptimizer] {symbol}/{timeframe} slope bounds: "
            f"min={_slope_bounds['vwap_slope_min']:.6f} "
            f"max={_slope_bounds['vwap_slope_max']:.6f} (live-calibrated from {len(df)} bars)"
        )

        # 2. EMA period sanity: ensure trend EMA max does not exceed available history.
        if _ema_bounds["trend_ema_max"] >= len(df):
            logger.warning(
                f"Trend EMA max ({_ema_bounds['trend_ema_max']}) exceeds candle count "
                f"({len(df)}) for {_tf_for_bounds}. Clamping to {len(df) - 1}."
            )
            _ema_bounds["trend_ema_max"] = max(
                _ema_bounds["trend_ema_min"] + 1, len(df) - 1
            )

        # 3. Trade-density estimate: skipped until a reliable forecast exists.

        # 4. Parameter bounds consistency.
        for name, minv, maxv in [
            ("fast EMA", _ema_bounds["fast_ema_min"], _ema_bounds["fast_ema_max"]),
            ("slow EMA", _ema_bounds["slow_ema_min"], _ema_bounds["slow_ema_max"]),
            ("trend EMA", _ema_bounds["trend_ema_min"], _ema_bounds["trend_ema_max"]),
            (
                "VWAP slope",
                _slope_bounds["vwap_slope_min"],
                _slope_bounds["vwap_slope_max"],
            ),
        ]:
            if minv >= maxv:
                raise ValueError(
                    f"{name} bounds invalid: min ({minv}) >= max ({maxv})."
                )
            if minv < 0:
                raise ValueError(
                    f"{name} min ({minv}) < 0. Ensure non-negative bounds."
                )
        # ---- end of validation ----

        opt_config = OptunaConfig(
            execution_mode=cfg.execution_mode,
            n_workers=cfg.n_workers,
            n_trials=cfg.n_trials,
            target_metric=optuna_target,
            min_trades=cfg.min_trades,
            sampler_seed=cfg.sampler_seed,
            seed_base_params=cfg.seed_base_params,
            multivariate=True,
            enable_multi_objective=cfg.enable_multi_objective,
            multi_objective_metrics=cfg.multi_objective_metrics,
            enable_hard_drawdown_constraint=cfg.enable_hard_drawdown_constraint,
            max_drawdown_constraint_pct=cfg.max_drawdown_constraint_pct,
            enable_max_holding_bars=cfg.enable_max_holding_bars,
            max_holding_bars_min=cfg.max_holding_bars_min,
            max_holding_bars_max=cfg.max_holding_bars_max,
            fast_ema_min=_ema_bounds["fast_ema_min"],
            fast_ema_max=_ema_bounds["fast_ema_max"],
            slow_ema_min=_ema_bounds["slow_ema_min"],
            slow_ema_max=_ema_bounds["slow_ema_max"],
            trend_ema_min=_ema_bounds["trend_ema_min"],
            trend_ema_max=_ema_bounds["trend_ema_max"],
            volume_multiplier_min=cfg.volume_multiplier_min,
            volume_multiplier_max=cfg.volume_multiplier_max,
            atr_multiplier_min=cfg.atr_multiplier_min,
            atr_multiplier_max=cfg.atr_multiplier_max,
            risk_reward_min=cfg.risk_reward_min,
            risk_reward_max=cfg.risk_reward_max,
            vwap_slope_min=_slope_bounds["vwap_slope_min"],
            vwap_slope_max=_slope_bounds["vwap_slope_max"],
            pullback_tolerance_min=cfg.pullback_tolerance_min,
            pullback_tolerance_max=cfg.pullback_tolerance_max,
        )
        optimizer = OptunaOptimizer(base_params=base_params, config=opt_config)

        # optimize() explicitly accepts is_cancelled and progress_callback with
        # default None (ema_vwap-agfy). Pass directly; no signature introspection needed.
        result = optimizer.optimize(
            df,
            is_cancelled=is_cancelled,
            progress_callback=progress_callback,
        )

        if not result or result.get("best_params") is None:
            print(f"[BatchOptimizer] No result for {symbol}/{timeframe}; skipping.")
            return
        if result.get("best_value_valid") is False:
            print(
                f"[BatchOptimizer] No valid optimization trial for "
                f"{symbol}/{timeframe}; skipping."
            )
            return

        metrics: dict = result.get("best_backtest_result") or {}
        trade_count = int(metrics.get("total_trades", 0))
        if trade_count < cfg.min_trades:
            print(
                f"[BatchOptimizer] Skipping {symbol}/{timeframe}: "
                f"only {trade_count} trades (configured min {cfg.min_trades})"
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


def _run_all_metrics_batch_task(
    exchange: str,
    symbols: list[str],
    timeframes: list[str],
    strategy_mode: str,
    trade_direction: str,
    n_trials: int,
    n_jobs: int,
    timeout: int | None,
    days: int | None,
    min_candles: int,
    random_seed: int | None,
    task_id: str,
) -> None:
    """Background task for all_metrics batch sweep."""
    results = []
    for metric in ALL_SINGLE_METRICS:
        cfg = BatchOptimizerConfig(
            exchange=exchange,
            symbols=symbols,
            timeframes=timeframes,
            strategy_mode=strategy_mode,
            trade_direction=trade_direction,
            target_metric=metric,
            n_trials=n_trials,
            n_jobs=n_jobs,
            timeout=timeout,
            days=days,
            min_candles=min_candles,
            random_seed=random_seed,
        )
        optimizer = BatchOptimizer(cfg)
        optimizer.run(
            reset_state=False,
            done_offset=0,
            total_override=len(ALL_SINGLE_METRICS) * len(symbols) * len(timeframes),
            combo_label_prefix=f"[{metric}] ",
            mark_complete=False,
        )
        results.extend(db.load_batch_results(target_metric=metric))
    _update_state(running=False, done=1, total=1)


def _run_all_combinations_batch_task(
    exchange: str,
    symbols: list[str],
    timeframes: list[str],
    strategy_mode: str,
    trade_direction: str,
    n_trials: int,
    n_jobs: int,
    timeout: int | None,
    days: int | None,
    min_candles: int,
    random_seed: int | None,
    task_id: str,
) -> None:
    """Background task for all_combinations batch sweep."""
    for metric_pair in ALL_CURATED_MULTI_PAIRS:
        cfg = BatchOptimizerConfig(
            exchange=exchange,
            symbols=symbols,
            timeframes=timeframes,
            strategy_mode=strategy_mode,
            trade_direction=trade_direction,
            target_metric="|".join(metric_pair),
            n_trials=n_trials,
            n_jobs=n_jobs,
            timeout=timeout,
            days=days,
            min_candles=min_candles,
            enable_multi_objective=True,
            multi_objective_metrics=metric_pair,
            random_seed=random_seed,
        )
        optimizer = BatchOptimizer(cfg)
        optimizer.run(
            reset_state=False,
            done_offset=0,
            total_override=len(ALL_CURATED_MULTI_PAIRS)
            * len(symbols)
            * len(timeframes),
            combo_label_prefix=f"[{'|'.join(metric_pair)}] ",
            mark_complete=False,
        )
    _update_state(running=False, done=1, total=1)
