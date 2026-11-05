"""
Walk-Forward Optimization (WFO) Engine.
Executes rolling / anchored in-sample (IS) parameter tuning and out-of-sample (OOS) validation to prevent strategy overfitting.
"""

import logging
import math
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from src.backtester import BacktestEngine
from src.config import OptunaConfig, StrategyParams, WFOConfig
from src.optimizer import OptunaOptimizer
from src.regime import INSUFFICIENT_DATA, RegimeConfig, classify_regime

logger = logging.getLogger(__name__)


def get_default_target_oos_days(
    timeframe: str | None = None,
    bars_per_day: float | None = None,
) -> float:
    """
    Return recommended out-of-sample (OOS) window duration in calendar days.
    Scales based on timeframe / candles-per-day to ensure sufficient trade count:
    - <= 5m: 15 days
    - 15m - 30m: 25 days
    - 1h - 2h: 45 days
    - 4h - 6h: 60 days
    - 8h - 12h: 90 days
    - 1d: 120 days
    """
    if timeframe:
        tf = timeframe.lower().strip()
        tf_oos = {
            "1m": 10.0,
            "3m": 15.0,
            "5m": 15.0,
            "15m": 25.0,
            "30m": 25.0,
            "1h": 45.0,
            "2h": 45.0,
            "4h": 60.0,
            "6h": 60.0,
            "8h": 90.0,
            "12h": 90.0,
            "1d": 120.0,
        }
        if tf in tf_oos:
            return tf_oos[tf]

    if bars_per_day is not None and bars_per_day > 0:
        if bars_per_day >= 288.0:
            return 15.0
        elif bars_per_day >= 48.0:
            return 25.0
        elif bars_per_day >= 12.0:
            return 45.0
        elif bars_per_day >= 4.0:
            return 60.0
        elif bars_per_day > 1.0:
            return 90.0
        else:
            return 120.0

    return 15.0


def derive_bars_per_day(
    df: pd.DataFrame | None = None,
    timeframe: str | None = None,
    category: str = "crypto",
) -> float:
    """
    Derive candles per day from timeframe name or dataframe timestamp diffs.
    Falls back to 288.0 (5m crypto default) if cannot be determined.
    """
    if timeframe:
        tf = timeframe.lower().strip()
        tf_bars = {
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
        if tf in tf_bars:
            return tf_bars[tf]

    if df is not None and not df.empty and "timestamp" in df.columns and len(df) >= 2:
        try:
            ts_series = df["timestamp"].iloc[:50]
            if pd.api.types.is_numeric_dtype(ts_series):
                max_val = float(ts_series.max())
                unit = "ms" if max_val > 1e11 else "s"
                parsed_ts = pd.to_datetime(ts_series, unit=unit, utc=True)
            else:
                parsed_ts = pd.to_datetime(ts_series, utc=True)

            diffs = parsed_ts.diff().dropna().dt.total_seconds()
            med_sec = float(diffs.median())
            if med_sec > 0:
                bpd = round(86400.0 / med_sec, 2)
                if bpd > 0:
                    return bpd
        except Exception as e:
            logger.debug("bars-per-day estimation failed, defaulting to 288.0: %s", e)

    return 288.0


def compute_wfo_auto_params(
    n_bars: int,
    n_trials: int,
    is_ratio: float = 0.7,
    target_oos_days: float | None = None,
    bars_per_day: float | None = None,
    timeframe: str | None = None,
    min_trials_floor: int = 25,
    target_trials: int = 50,
    explicit_num_windows: int | None = None,
) -> dict[str, int]:
    """
    Derive optimal WFO num_windows and trials_per_window from dataset size and
    Optuna trial budget using intelligent auto-balancing.

    Strategy:
    - Target OOS window length of ``target_oos_days`` trading days.
    - Compute data-supported window ceiling:
        data_windows = floor(n_bars / oos_target_bars - ratio_factor), clamped [3, 20].
    - If explicit_num_windows is provided (> 0), use that window count and derive
      trials_per_window = max(min_trials_floor, math.floor(n_trials / explicit_num_windows)).
    - Otherwise, intelligently auto-balance num_windows and trials_per_window:
        1. If budget is abundant (n_trials >= data_windows * target_trials):
           num_windows = data_windows
           trials_per_window = math.floor(n_trials / num_windows)
        2. If budget can support >= 3 windows with target_trials (50) each:
           num_windows = min(data_windows, math.floor(n_trials / target_trials))
           trials_per_window = math.floor(n_trials / num_windows)
        3. If budget can support >= 3 windows with min_trials_floor (25) each:
           num_windows = min(data_windows, math.floor(n_trials / min_trials_floor))
           trials_per_window = math.floor(n_trials / num_windows)
        4. When budget is severely constrained (< 3 * min_trials_floor):
           num_windows = 3
           trials_per_window = max(min_trials_floor, math.floor(n_trials / num_windows))

    Args:
        n_bars: Total number of candles in the dataset.
        n_trials: Optuna trial budget (e.g. the value from the UI).
        is_ratio: Fraction of each window allocated to in-sample training.
        target_oos_days: Desired OOS window length in trading days.
        bars_per_day: Candles per trading day (288 for 5m, 96 for 15m, 24 for 1h).
        timeframe: Optional timeframe string (e.g. "5m", "15m", "1h", "4h").
        min_trials_floor: Absolute floor for trials per window (default: 25).
        target_trials: Recommended minimum trials per window for convergence (default: 50).
        explicit_num_windows: Optional pre-set number of windows.

    Returns:
        Dict with keys ``num_windows`` and ``trials_per_window``.
    """
    if bars_per_day is None or bars_per_day <= 0:
        bars_per_day = derive_bars_per_day(timeframe=timeframe)

    if target_oos_days is None or target_oos_days <= 0:
        target_oos_days = get_default_target_oos_days(
            timeframe=timeframe, bars_per_day=bars_per_day
        )

    oos_target_bars = max(15, int(target_oos_days * bars_per_day))

    # Mirror the formula used in generate_windows() so num_windows produces
    # the desired OOS step size:
    #   oos_step = n_bars / (ratio_factor + num_windows)
    # Rearranged: num_windows = n_bars / oos_target_bars - ratio_factor
    ratio_factor = is_ratio / (1.0 - is_ratio)
    raw_windows = math.floor(n_bars / oos_target_bars - ratio_factor)
    data_windows = max(3, min(20, raw_windows))

    if explicit_num_windows is not None and explicit_num_windows > 0:
        num_windows = explicit_num_windows
        trials_per_window = max(min_trials_floor, math.floor(n_trials / num_windows))
        return {"num_windows": num_windows, "trials_per_window": trials_per_window}

    # Auto-balancing based on budget and data capacity:
    # 1. Abundant budget: run full data-supported windows at >= target_trials each
    if n_trials >= data_windows * target_trials:
        num_windows = data_windows
        trials_per_window = math.floor(n_trials / num_windows)
    # 2. Moderate budget: support as many windows as possible at >= target_trials (50) each
    elif n_trials >= 3 * target_trials:
        num_windows = min(data_windows, math.floor(n_trials / target_trials))
        trials_per_window = math.floor(n_trials / num_windows)
    # 3. Constrained budget: scale down windows to guarantee >= min_trials_floor (25) each
    elif n_trials >= 3 * min_trials_floor:
        num_windows = min(data_windows, math.floor(n_trials / min_trials_floor))
        trials_per_window = math.floor(n_trials / num_windows)
    # 4. Severely constrained budget (< 75 trials total):
    else:
        num_windows = 3
        trials_per_window = max(min_trials_floor, math.floor(n_trials / num_windows))

    return {"num_windows": num_windows, "trials_per_window": trials_per_window}


class WalkForwardEngine:
    """Walk-Forward Optimization Engine managing IS training and OOS testing windows."""

    def __init__(
        self,
        base_params: StrategyParams,
        wfo_config: WFOConfig | None = None,
        opt_config: OptunaConfig | None = None,
        n_trials_total: int = 0,
        bars_per_day: float | None = None,
        timeframe: str | None = None,
    ):
        self.base_params = base_params
        self.wfo_config = wfo_config or WFOConfig()
        self.n_trials_total = (
            n_trials_total  # full Optuna budget from UI, used for auto-scaling
        )
        self.timeframe = timeframe
        if bars_per_day is not None and bars_per_day > 0:
            self.bars_per_day = bars_per_day
        elif timeframe:
            self.bars_per_day = derive_bars_per_day(timeframe=timeframe)
        else:
            self.bars_per_day = 288.0

        # Guard: if trials_per_window is 0 (auto sentinel), use a safe fallback
        # until run() resolves the real value via compute_wfo_auto_params.
        effective_trials = self.wfo_config.trials_per_window or 50
        self.opt_config = opt_config or OptunaConfig(n_trials=effective_trials)

    def generate_windows(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        """
        Generate sliding (rolling) or expanding (anchored) In-Sample (IS) training
        and contiguous, non-overlapping Out-of-Sample (OOS) testing candle slices.
        """
        n_bars = len(df)
        num_windows = self.wfo_config.num_windows
        is_ratio = max(0.5, min(0.9, self.wfo_config.in_sample_ratio))
        window_type = self.wfo_config.window_type

        min_is_bars = 40
        min_oos_bars = 15

        if n_bars < min_is_bars + 2 * min_oos_bars:
            return []

        # Ensure num_windows fits within dataset
        max_possible_windows = max(2, (n_bars - min_is_bars) // min_oos_bars)
        num_windows = min(num_windows, max_possible_windows)

        # Determine optimal OOS step size (O) and initial IS size (I)
        # N = I + W * O, where I / (I + O) approx is_ratio => I approx O * (is_ratio / (1 - is_ratio))
        # N = O * (is_ratio / (1 - is_ratio) + W)
        ratio_factor = is_ratio / (1.0 - is_ratio)
        oos_step = int(n_bars / (ratio_factor + num_windows))
        oos_step = max(min_oos_bars, oos_step)

        is_len = n_bars - (num_windows * oos_step)
        if is_len < min_is_bars:
            # Fallback allocation
            oos_step = max(min_oos_bars, (n_bars - min_is_bars) // num_windows)
            is_len = n_bars - (num_windows * oos_step)

        windows = []
        for i in range(num_windows):
            oos_start = is_len + (i * oos_step)
            # Last window extends to end of dataset to avoid unallocated bars
            oos_end = n_bars if (i == num_windows - 1) else (oos_start + oos_step)

            if window_type == "rolling":
                is_start = i * oos_step
                is_end = oos_start
            else:  # anchored (expanding window)
                is_start = 0
                is_end = oos_start

            df_is = df.iloc[is_start:is_end].copy()
            df_oos = df.iloc[oos_start:oos_end].copy()
            df_window = df.iloc[is_start:oos_end].copy()

            if len(df_is) < min_is_bars or len(df_oos) < 5:
                continue

            windows.append(
                {
                    "window_index": i + 1,
                    "df_is": df_is,
                    "df_oos": df_oos,
                    "df_window": df_window,
                    "warmup_bars": len(df_is),
                    "is_start": str(df_is["timestamp"].iloc[0]),
                    "is_end": str(df_is["timestamp"].iloc[-1]),
                    "oos_start": str(df_oos["timestamp"].iloc[0]),
                    "oos_end": str(df_oos["timestamp"].iloc[-1]),
                }
            )

        return windows

    def _detect_window_regime(
        self, df_window: pd.DataFrame, config: RegimeConfig | None = None
    ) -> str:
        """Detect regime for a given data window.

        Returns one of: 'trending', 'ranging', 'high_vol', or 'insufficient_data'.
        Used for regime-aware re-optimization triggers.
        """
        cfg = config or RegimeConfig()
        regime_series = classify_regime(df_window, config=cfg)
        last = regime_series.iloc[-1]
        if last == INSUFFICIENT_DATA or pd.isna(last):
            return INSUFFICIENT_DATA
        return str(last)

    def run(self, df: pd.DataFrame) -> dict[str, Any]:
        """Execute full Walk-Forward Optimization across all generated windows."""
        if df.empty or len(df) < 100:
            raise ValueError(
                "Insufficient candle data for Walk-Forward Optimization (minimum 100 bars required)."
            )

        # Derive bars_per_day if not explicitly provided or if default 288.0 and timeframe is absent
        if self.bars_per_day == 288.0 and self.timeframe is None:
            self.bars_per_day = derive_bars_per_day(df=df, timeframe=self.timeframe)

        # Auto-scale num_windows / trials_per_window when sentinel value 0 is set
        auto_scaled: dict[str, int] = {}
        needs_auto = (
            self.wfo_config.num_windows == 0 or self.wfo_config.trials_per_window == 0
        )
        if needs_auto:
            budget = self.n_trials_total if self.n_trials_total > 0 else 100
            auto_scaled = compute_wfo_auto_params(
                n_bars=len(df),
                n_trials=budget,
                is_ratio=max(0.5, min(0.9, self.wfo_config.in_sample_ratio)),
                bars_per_day=self.bars_per_day,
                timeframe=self.timeframe,
                explicit_num_windows=(
                    self.wfo_config.num_windows
                    if self.wfo_config.num_windows > 0
                    else None
                ),
            )
            resolved_windows = (
                auto_scaled["num_windows"]
                if self.wfo_config.num_windows == 0
                else self.wfo_config.num_windows
            )
            resolved_trials = (
                auto_scaled["trials_per_window"]
                if self.wfo_config.trials_per_window == 0
                else self.wfo_config.trials_per_window
            )
            self.wfo_config = WFOConfig(
                num_windows=resolved_windows,
                in_sample_ratio=self.wfo_config.in_sample_ratio,
                window_type=self.wfo_config.window_type,
                trials_per_window=resolved_trials,
            )

        # Always sync opt_config.n_trials with the resolved trials_per_window
        # (covers both auto-scaled and manually-set cases where __init__ used a fallback)
        self.opt_config = replace(
            self.opt_config,
            n_trials=self.wfo_config.trials_per_window,
        )

        windows = self.generate_windows(df)
        if not windows:
            raise ValueError(
                "Could not construct valid IS/OOS windows with provided candle data length."
            )

        # --- Regime-aware analysis across windows ---
        window_regimes: list[str] = []
        regime_changes: int = 0
        for win in windows:
            df_window = win.get("df_window")
            if df_window is not None and len(df_window) >= 60:
                regime = self._detect_window_regime(df_window)
            else:
                regime = "insufficient_data"
            window_regimes.append(regime)
            # Count regime changes (excluding insufficient_data sentinel)
            if len(window_regimes) > 1:
                prev = window_regimes[-2]
                cur = window_regimes[-1]
                if (
                    prev != "insufficient_data"
                    and cur != "insufficient_data"
                    and prev != cur
                ):
                    regime_changes += 1

        # Re-optimization trigger: flag if regime changed significantly
        # (more than half the windows shifted regime)
        n_windows = len(windows)
        regime_change_ratio = regime_changes / n_windows if n_windows > 0 else 0.0
        trigger_reoptimize = regime_change_ratio > 0.5

        # (existing logic continues below)
        window_results = []
        is_returns = []
        oos_returns = []
        combined_oos_trades = []

        for win in windows:
            df_is = win["df_is"]
            df_window = win.get("df_window")
            warmup_bars = win.get("warmup_bars", len(df_is))

            # Step 1: Optimize parameters on In-Sample (IS) data
            optimizer = OptunaOptimizer(self.base_params, self.opt_config)
            opt_res = optimizer.optimize(df_is)
            best_params = StrategyParams(**opt_res["best_params"])
            is_best_result = opt_res["best_backtest_result"]

            # Step 2: Validate best parameters on Out-Of-Sample (OOS) data with warm-up history
            oos_engine = BacktestEngine(best_params)
            if df_window is not None:
                oos_result = oos_engine.run(df_window, warmup_bars=warmup_bars)
            else:
                oos_result = oos_engine.run(win["df_oos"])

            is_ret = is_best_result.get("total_return_pct", 0.0)
            oos_ret = oos_result.total_return_pct

            is_returns.append(is_ret)
            oos_returns.append(oos_ret)
            combined_oos_trades.extend(oos_result.trades)

            window_results.append(
                {
                    "window_index": win["window_index"],
                    "is_period": f"{win['is_start']} to {win['is_end']}",
                    "oos_period": f"{win['oos_start']} to {win['oos_end']}",
                    "best_params": best_params.to_dict(),
                    "is_metric_target": self.opt_config.target_metric,
                    "is_value": (
                        opt_res["best_value"]
                        if opt_res["best_value"] is not None
                        else 0.0
                    ),
                    "is_return_pct": is_ret,
                    "is_win_rate": is_best_result.get("win_rate", 0.0),
                    "is_sharpe": is_best_result.get("sharpe_ratio", 0.0),
                    "oos_return_pct": oos_ret,
                    "oos_win_rate": oos_result.win_rate,
                    "oos_profit_factor": oos_result.profit_factor,
                    "oos_max_drawdown_pct": oos_result.max_drawdown_pct,
                    "oos_sharpe": oos_result.sharpe_ratio,
                    "oos_trades_count": oos_result.total_trades,
                    # Regime tracking fields
                    "window_regime": (
                        window_regimes[len(window_results)]
                        if len(window_results) < len(window_regimes)
                        else "insufficient_data"
                    ),
                    "regime_change_trigger": trigger_reoptimize,
                }
            )

        # Calculate Walk-Forward Efficiency (WFE) ratio with time normalization
        avg_is_ret = float(np.mean(is_returns)) if is_returns else 0.0
        avg_oos_ret = float(np.mean(oos_returns)) if oos_returns else 0.0

        is_bars_list = [len(w["df_is"]) for w in windows]
        oos_bars_list = [len(w["df_oos"]) for w in windows]
        avg_is_bars = float(np.mean(is_bars_list)) if is_bars_list else 1.0
        avg_oos_bars = float(np.mean(oos_bars_list)) if oos_bars_list else 1.0
        time_scale = (avg_is_bars / avg_oos_bars) if avg_oos_bars > 0 else 1.0

        if avg_is_ret > 0:
            # Time-normalized WFE: scale by ratio of IS window bars to OOS window bars
            # so that unequal IS/OOS durations (e.g. 70/30 split) do not depress efficiency
            wfe_ratio = round(
                ((avg_oos_ret / avg_oos_bars) / (avg_is_ret / avg_is_bars)) * 100.0, 2
            )
        elif avg_is_ret < 0 and avg_oos_ret >= 0:
            # IS was negative but OOS recovered — treat as 100% efficient (beat negative baseline)
            wfe_ratio = 100.0
        else:
            # avg_is_ret == 0, or both IS and OOS are negative (comparison is meaningless)
            wfe_ratio = 0.0

        # Parameter consistency metric (how often parameters stay within bounds across windows)
        param_variance = {}
        for param_key in [
            "fast_ema",
            "slow_ema",
            "trend_ema",
            "volume_multiplier",
            "atr_multiplier",
            "risk_reward_ratio",
        ]:
            vals = [
                w["best_params"][param_key]
                for w in window_results
                if param_key in w["best_params"]
            ]
            if vals:
                param_variance[param_key] = round(float(np.std(vals)), 4)

        oos_cumulative_ret = (
            round((np.prod([1 + r / 100.0 for r in oos_returns]) - 1) * 100.0, 2)
            if oos_returns
            else 0.0
        )

        return {
            "window_count": len(window_results),
            "walk_forward_efficiency_pct": wfe_ratio,
            "avg_in_sample_return_pct": round(avg_is_ret, 2),
            "avg_out_of_sample_return_pct": round(avg_oos_ret, 2),
            "oos_total_return_pct": oos_cumulative_ret,
            "oos_cumulative_return_pct": oos_cumulative_ret,
            "time_scale": round(time_scale, 2),
            "avg_is_bars": round(avg_is_bars, 1),
            "avg_oos_bars": round(avg_oos_bars, 1),
            "bars_per_day": round(self.bars_per_day, 2),
            "timeframe": self.timeframe,
            "param_stability_std": param_variance,
            "window_details": window_results,
            "combined_oos_trade_count": len(combined_oos_trades),
            "auto_scaled_params": auto_scaled if auto_scaled else None,
            "effective_num_windows": self.wfo_config.num_windows,
            "effective_trials_per_window": self.wfo_config.trials_per_window,
            # New regime-aware fields
            "window_regimes": window_regimes,
            "regime_change_count": regime_changes,
            "regime_change_ratio": round(regime_change_ratio, 2),
            "trigger_reoptimize": trigger_reoptimize,
        }
