"""
Batch Optimizer Module — Multi-coin, Multi-timeframe Optuna Sweep.

Iterates over a configurable list of symbols × timeframes, runs Optuna
optimization on each combination, and persists results to the SQLite
batch_results leaderboard table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg
import pandas as pd

from src.config import (
    MAX_OPTUNA_WORKERS,
    OptunaConfig,
    StrategyParams,
    get_optimal_lookback_days,
)
from src.data_loader import DataLoader, get_donor_timeframe
from src.database import get_database
from src.logger import logger
from src.metrics import (
    BATCH_OPTIMIZER_ACTIVE,
    BATCH_OPTIMIZER_TRIAL_DURATION,
    BATCH_OPTIMIZER_TRIALS,
    BATCH_OPTIMIZER_TRIALS_PER_SECOND,
)
from src.optimizer import OptunaOptimizer
from src.pg_database import load_batch_results_pg, save_batch_result_pg
from src.risk_manager import BACKTEST_RISK_MODEL_REVISION
from src.tasks import TaskCancelledException

db = get_database("batch_results")

# Transient DB failures worth retrying (chaos: PG restart mid-batch).
# asyncpg raises PostgresError/InterfaceError subclasses for dead connections;
# OSError/ConnectionError cover TCP-level failures during a container restart.
_TRANSIENT_DB_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
)

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
    vwap_slope_max_bound: float = (
        0.003  # Deprecated: use vwap_slope_max instead (ixoi.6)
    )
    n_jobs: int = 1
    n_startup_trials: int | None = None
    multivariate: bool = True
    sampler_seed: int | None = 42
    seed_base_params: bool = True
    enable_multi_objective: bool = False
    multi_objective_metrics: list[str] = field(
        default_factory=lambda: ["sharpe_ratio", "max_drawdown_pct"]
    )
    # Pruner: early-stop unpromising trials (default: none; use "median" for 10-30 params, "hyperband" for >30 params)
    pruner: Literal["median", "hyperband", "none"] = "none"
    enable_hard_drawdown_constraint: bool = False
    max_drawdown_constraint_pct: float = 25.0
    enable_max_holding_bars: bool = False
    max_holding_bars_min: int = 4
    max_holding_bars_max: int = 500
    trade_direction: str = "long_only"
    min_candles: int = 500  # skip combos with fewer bars than this

    # Chaos recovery (ema_vwap-x27p): transient DB failures (e.g. PG restart
    # mid-batch) are retried with linear backoff before the combo is aborted.
    db_retry_attempts: int = 5
    db_retry_delay_seconds: float = 3.0

    # Pullback tolerance range (default to OptunaConfig defaults)
    pullback_tolerance_min: float = 0.05
    pullback_tolerance_max: float = 1.5

    # VWAP slope range (ixoi.6: previously only max_bound existed and was never forwarded)
    vwap_slope_min: float = 0.00001
    vwap_slope_max: float = 0.003  # replaces old vwap_slope_max_bound

    # VWAP stop offset range
    vwap_stop_offset_min: float = 0.02
    vwap_stop_offset_max: float = 0.8

    # Risk per trade range
    risk_per_trade_min: float = 0.5
    risk_per_trade_max: float = 3.0

    # Trailing stop range
    trailing_stop_min: float = 1.0
    trailing_stop_max: float = 3.0

    # Portfolio position cap range
    max_position_pct_min: float = 25.0
    max_position_pct_max: float = 100.0

    # Phase 1 (ema_vwap-0w9): regime-aware parameter adaptation
    regime_aware_params: bool = (
        False  # flexible (same ranges, regime-aware initial/default values)
    )

    def __post_init__(self) -> None:
        if self.execution_mode not in {"sequential", "multiprocess"}:
            raise ValueError(
                f"Invalid execution_mode '{self.execution_mode}'. Must be 'sequential' or 'multiprocess'."
            )
        if self.execution_mode == "sequential":
            self.n_workers = 1
        else:
            self.n_workers = min(max(1, int(self.n_workers)), MAX_OPTUNA_WORKERS)

        # Handle pipe-separated target_metric (e.g., "calmar_ratio|max_drawdown_pct")
        # This enables multi-objective optimization automatically
        if "|" in self.target_metric:
            metrics = [m.strip() for m in self.target_metric.split("|")]
            self.enable_multi_objective = True
            self.multi_objective_metrics = metrics


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

    def _checkpoint_fingerprint(self, timeframe: str) -> str:
        """Return a stable identity for results reusable by this batch setup."""
        cfg = self.config
        batch_config = asdict(cfg)
        for key in (
            "symbols",
            "timeframes",
            "db_retry_attempts",
            "db_retry_delay_seconds",
        ):
            batch_config.pop(key, None)

        effective_days = cfg.days
        if effective_days is None:
            effective_days = get_optimal_lookback_days(timeframe, "crypto")
        batch_config["days"] = effective_days

        payload = {
            "fingerprint_schema": 1,
            "backtest_risk_model_revision": BACKTEST_RISK_MODEL_REVISION,
            "timeframe": timeframe.lower(),
            "effective_lookback_days": effective_days,
            "batch_config": batch_config,
            "base_strategy_params": StrategyParams(
                strategy_mode=cfg.strategy_mode,
                trade_direction=cfg.trade_direction,
            ).to_dict(),
        }
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    async def _get_base_candles(
        self, symbol: str, timeframe: str
    ) -> pd.DataFrame | None:
        """Return base (finest) timeframe candles for a symbol, fetching once.

        For multi-timeframe sweeps on the same symbol, we fetch each timeframe
        natively when supported, otherwise resample from the finest native timeframe.
        When resampling, we fetch the base timeframe with enough days to cover
        the target timeframe's optimal lookback.

        Runs on the main event loop (BatchOptimizer.run() executes there), so
        we await DataLoader.load_candles directly — the sync bridge (run_async)
        is forbidden on the main loop and would raise a RuntimeError.
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

            df = await DataLoader.load_candles(
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

        df = await DataLoader.load_candles(
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

    async def _run_single_optuna(
        self,
        symbol: str,
        timeframe: str,
        is_cancelled: Callable[[], bool] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run Optuna optimization for a single symbol/timeframe combo."""
        cfg = self.config

        # Load candles for this symbol/timeframe (handles donor resampling)
        df = await self._get_base_candles(symbol, timeframe)
        if df is not None and not df.empty:
            donor = get_donor_timeframe(cfg.exchange.lower(), timeframe)
            if donor:
                df = self._resample_from_base(df, timeframe)

        if df is None or df.empty or len(df) < cfg.min_candles:
            logger.warning(
                f"[BatchOptimizer] Insufficient data for {symbol} {timeframe}: "
                f"{len(df) if df is not None else 0} bars"
            )
            return {"status": "error", "message": "Insufficient candle data"}

        # Create base params for this symbol/timeframe.
        # Explicitly propagate strategy_mode and trade_direction from the batch
        # config so Optuna tunes the correct mode and direction (previously
        # defaulted to "crossover"/"long_only" regardless of cfg values).
        base_params = StrategyParams(
            strategy_mode=cfg.strategy_mode,
            trade_direction=cfg.trade_direction,
        )

        # Create Optuna config from batch config
        is_multi = cfg.enable_multi_objective or ("|" in cfg.target_metric)
        multi_metrics = cfg.multi_objective_metrics
        if "|" in cfg.target_metric and not cfg.enable_multi_objective:
            multi_metrics = [
                m.strip() for m in cfg.target_metric.split("|") if m.strip()
            ]
        primary_metric = (
            multi_metrics[0] if (is_multi and multi_metrics) else cfg.target_metric
        )

        optuna_config = OptunaConfig(
            n_trials=cfg.n_trials,
            target_metric=primary_metric,
            enable_multi_objective=is_multi,
            multi_objective_metrics=multi_metrics,
            n_jobs=1,  # Single-threaded per combo
            n_startup_trials=cfg.n_startup_trials,
            multivariate=cfg.multivariate,
            min_trades=cfg.min_trades,
            sampler_seed=cfg.sampler_seed,
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
            pullback_tolerance_min=cfg.pullback_tolerance_min,
            pullback_tolerance_max=cfg.pullback_tolerance_max,
            enable_max_holding_bars=cfg.enable_max_holding_bars,
            max_holding_bars_min=cfg.max_holding_bars_min,
            max_holding_bars_max=cfg.max_holding_bars_max,
            enable_hard_drawdown_constraint=cfg.enable_hard_drawdown_constraint,
            max_drawdown_constraint_pct=cfg.max_drawdown_constraint_pct,
            # ixoi.6: previously missing — batch sweeps now respect these bounds
            vwap_slope_min=cfg.vwap_slope_min,
            vwap_slope_max=cfg.vwap_slope_max,
            vwap_stop_offset_min=cfg.vwap_stop_offset_min,
            vwap_stop_offset_max=cfg.vwap_stop_offset_max,
            risk_per_trade_min=cfg.risk_per_trade_min,
            risk_per_trade_max=cfg.risk_per_trade_max,
            trailing_stop_min=cfg.trailing_stop_min,
            trailing_stop_max=cfg.trailing_stop_max,
            max_position_pct_min=cfg.max_position_pct_min,
            max_position_pct_max=cfg.max_position_pct_max,
            # Pruner: early-stop unpromising trials (ema_vwap-0w9 / user request)
            pruner=cfg.pruner,
        )

        # Run optimization (offload CPU-heavy Optuna trials to thread pool)
        optimizer = OptunaOptimizer(base_params=base_params, config=optuna_config)
        result = await asyncio.to_thread(
            optimizer.optimize,
            df=df,
            is_cancelled=is_cancelled,
            progress_callback=progress_callback,
        )

        # Save result to database
        if result.get("status") != "cancelled" and result.get("best_params"):
            # Ensure best_value is never None (PG column has NOT NULL constraint)
            # result.get("best_value", 0.0) returns None if key exists with None value,
            # so explicitly coerce None -> 0.0
            raw_best = result.get("best_value")
            best_val = 0.0 if raw_best is None else float(raw_best)

            best_bt = result.get("best_backtest_result") or {}
            if not best_bt and df is not None and not df.empty:
                try:
                    from src.backtester import BacktestEngine

                    bp = StrategyParams(**result["best_params"])
                    best_bt = BacktestEngine(bp).run(df).to_dict()
                except Exception as e:
                    logger.debug(f"[BatchOptimizer] Fallback backtest failed: {e}")

            trade_count = int(best_bt.get("total_trades", 0))

            sharpe_ratio = best_bt.get("sharpe_ratio")
            if sharpe_ratio is None and cfg.target_metric == "sharpe_ratio":
                sharpe_ratio = best_val

            calmar_ratio = best_bt.get("calmar_ratio")
            if calmar_ratio is None and cfg.target_metric == "calmar_ratio":
                calmar_ratio = best_val

            win_rate = best_bt.get("win_rate")
            if win_rate is None and cfg.target_metric == "win_rate":
                win_rate = best_val

            max_drawdown_pct = best_bt.get("max_drawdown_pct")
            if max_drawdown_pct is None and cfg.target_metric == "max_drawdown_pct":
                max_drawdown_pct = best_val

            total_return_pct = best_bt.get("total_return_pct")
            if total_return_pct is None and cfg.target_metric == "total_return_pct":
                total_return_pct = best_val

            await save_batch_result_pg(
                exchange=cfg.exchange,
                symbol=symbol,
                timeframe=timeframe,
                strategy_mode=cfg.strategy_mode,
                target_metric=cfg.target_metric,
                best_value=best_val,
                trade_count=trade_count,
                sharpe_ratio=sharpe_ratio,
                calmar_ratio=calmar_ratio,
                win_rate=win_rate,
                max_drawdown_pct=max_drawdown_pct,
                total_return_pct=total_return_pct,
                best_params=result["best_params"],
                run_timestamp=datetime.now(UTC),
                checkpoint_fingerprint=self._checkpoint_fingerprint(timeframe),
            )

        return result

    async def _load_checkpoint(self) -> set[tuple[str, str]]:
        """Load completed combos from checkpoint storage.

        Awaited directly (not via the sync bridge): run() executes on the
        app's main event loop, where run_async cannot block on the same
        loop it is called from — the bridge would raise and silently skip
        checkpoint resume (surfaced by the chaos tests, ema_vwap-x27p).
        """
        try:
            # Query batch_results for completed combos matching this setup's config.
            # The unique key is (exchange, symbol, timeframe, strategy_mode, target_metric).
            # Filter by all relevant config params to avoid cross-contamination between setups.
            results = []
            for timeframe in self.config.timeframes:
                results.extend(
                    await load_batch_results_pg(
                        target_metric=self.config.target_metric,
                        exchange=self.config.exchange,
                        symbols=self.config.symbols,
                        timeframes=[timeframe],
                        checkpoint_fingerprint=self._checkpoint_fingerprint(timeframe),
                    )
                )
            if results:
                return {
                    (r.get("symbol"), r.get("timeframe"))
                    for r in results
                    if r.get("symbol") and r.get("timeframe")
                    # Additional filter: strategy_mode must match (load_batch_results_pg doesn't filter this yet)
                    and r.get("strategy_mode") == self.config.strategy_mode
                }
        except Exception as e:
            logger.warning(f"[BatchOptimizer] Failed to load checkpoint: {e}")
        return set()

    def _save_checkpoint(self, completed_combos: set[tuple[str, str]]) -> None:
        """Save completed combos to checkpoint storage."""
        # Checkpoint is implicitly saved via save_batch_result_pg in _run_combo_with_db_retry
        # This method exists for interface completeness

    async def _load_final_results(self) -> list[dict[str, Any]]:
        """Load final leaderboard results with transient error retry."""
        cfg = self.config
        for _attempt in range(cfg.db_retry_attempts + 1):
            try:
                return await load_batch_results_pg(target_metric=cfg.target_metric)
            except _TRANSIENT_DB_ERRORS as exc:
                if _attempt >= cfg.db_retry_attempts:
                    raise
                _wait = cfg.db_retry_delay_seconds * (_attempt + 1)
                logger.warning(
                    "[BatchOptimizer] Transient DB error loading results "
                    "(attempt %d/%d): %s — retrying in %.0fs",
                    _attempt + 1,
                    cfg.db_retry_attempts,
                    exc,
                    _wait,
                )
                await asyncio.sleep(_wait)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _run_combo_with_db_retry(
        self,
        symbol: str,
        timeframe: str,
        _is_cancelled: Callable[[], bool],
        _combo_cb: Callable[[float, str], None],
        combo_str: str,
    ) -> dict[str, Any]:
        """Run one combo, retrying transient DB failures (e.g. PG restart).

        Chaotic-but-recoverable failures (dead pool connections, connection
        refused while the PG container restarts) are retried with linear
        backoff so a mid-batch PG restart does not abort the whole sweep.
        Cancellation stays responsive between attempts. Writes are idempotent
        (ON CONFLICT upsert), so a retried combo cannot corrupt batch_results.
        """
        cfg = self.config
        for attempt in range(cfg.db_retry_attempts + 1):
            if _is_cancelled():
                raise TaskCancelledException()
            try:
                result = await self._run_single_optuna(
                    symbol,
                    timeframe,
                    is_cancelled=_is_cancelled,
                    progress_callback=_combo_cb,
                )
                # Clear any transient error recorded during retries
                _update_state(error=None)
                return result
            except _TRANSIENT_DB_ERRORS as exc:
                if attempt >= cfg.db_retry_attempts:
                    logger.error(
                        "[BatchOptimizer] Combo %s failed after %d DB retries: %s",
                        combo_str,
                        cfg.db_retry_attempts,
                        exc,
                    )
                    raise
                wait = cfg.db_retry_delay_seconds * (attempt + 1)
                logger.warning(
                    "[BatchOptimizer] Transient DB error on %s (attempt %d/%d): %s"
                    " — retrying in %.0fs",
                    combo_str,
                    attempt + 1,
                    cfg.db_retry_attempts,
                    exc,
                    wait,
                )
                _update_state(
                    error=f"Transient DB error on {combo_str} "
                    f"(attempt {attempt + 1}/{cfg.db_retry_attempts}); retrying"
                )
                await asyncio.sleep(wait)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def run(
        self,
        is_cancelled: Callable[[], bool] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        reset_state: bool = True,
        done_offset: int = 0,
        total_override: int | None = None,
        combo_label_prefix: str = "",
        mark_complete: bool = True,
    ) -> list[dict[str, Any]]:
        """Run sweeps sequentially across all symbol x timeframe combos.

        Supports checkpointing for recovery from interruptions (PG restarts, crashes).
        Loads checkpoint at start, saves after each completed combo.
        """
        cfg = self.config
        combos = [(sym, tf) for sym in cfg.symbols for tf in cfg.timeframes]
        combos_count = len(combos)
        total = total_override if total_override is not None else combos_count

        # Load checkpoint if resuming
        completed_combos = await self._load_checkpoint()
        if completed_combos:
            logger.info(
                f"[BatchOptimizer] Resuming from checkpoint: {len(completed_combos)} combos already completed"
            )
            # Filter out already completed combos
            combos = [(s, t) for (s, t) in combos if (s, t) not in completed_combos]
            if not combos:
                logger.info(
                    "[BatchOptimizer] All combos already completed per checkpoint"
                )
                if mark_complete:
                    _update_state(
                        running=False,
                        done=total,
                        current_combo="Complete",
                        combo_progress=1.0,
                    )
                return await self._load_final_results()

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
            # Set batch optimizer as active
            BATCH_OPTIMIZER_ACTIVE.set(1)
            trial_start_time = time.perf_counter()

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

                await self._run_combo_with_db_retry(
                    symbol,
                    timeframe,
                    _is_cancelled=_is_cancelled,
                    _combo_cb=_combo_cb,
                    combo_str=combo_str,
                )
                # Update batch optimizer metrics
                elapsed = time.perf_counter() - trial_start_time
                BATCH_OPTIMIZER_TRIALS.labels(
                    symbol=symbol,
                    timeframe=timeframe,
                    target_metric=cfg.target_metric,
                    status="completed",
                ).inc()
                BATCH_OPTIMIZER_TRIAL_DURATION.labels(
                    symbol=symbol, timeframe=timeframe
                ).observe(elapsed)
                if elapsed > 0:
                    BATCH_OPTIMIZER_TRIALS_PER_SECOND.labels(
                        symbol=symbol, timeframe=timeframe
                    ).set((idx + 1) / elapsed)
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
            import traceback

            tb = traceback.format_exc()
            _update_state(running=False, error=str(exc), debug_error=tb)
            logger.error(f"[BatchOptimizer] Sweep failed with exception: {exc}\n{tb}")
        finally:
            # Reset active gauge
            BATCH_OPTIMIZER_ACTIVE.set(0)
        # Final leaderboard load: tolerate a PG restart landing on the finish
        # line (same transient-error retry semantics as the per-combo path).
        return await self._load_final_results()
