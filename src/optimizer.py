"""
Optuna Strategy Hyperparameter Optimization Module.
Tunes EMA periods, VWAP slope thresholds, ATR multipliers, and Risk/Reward parameters using TPE Sampler.
"""

import math
import warnings
from typing import Any

import optuna
import pandas as pd

from src.backtester import BacktestEngine
from src.config import OptunaConfig, StrategyParams
from src.logger import logger

# Suppress verbose Optuna logging during web API calls
optuna.logging.set_verbosity(optuna.logging.WARNING)
warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)

# Warm-start trial cache (ema_vwap-ddgr): in-memory best-trial params keyed by
# sweep identity (strategy_mode, metric, EMA bounds). Survives within the process
# only; a restart starts cold, which is the safe default.
_optuna_warmstart_cache: dict[tuple, list[dict]] = {}
_warmstart_max_trials = 10


class OptunaOptimizer:
    """Optuna hyperparameter tuner for EMA + VWAP strategy."""

    _TARGET_METRICS = frozenset(
        {
            "sharpe_ratio",
            "calmar_ratio",
            "sortino_ratio",
            "drawdown_penalized_sharpe",
            "drawdown_penalized_sortino",
            "ulcer_index",
            "profit_factor",
            "net_profit",
            "total_return_pct",
            "win_rate",
            "max_drawdown_pct",
        }
    )
    _MINIMIZE_METRICS = frozenset({"max_drawdown_pct", "ulcer_index"})

    def __init__(self, base_params: StrategyParams, config: OptunaConfig | None = None):
        self.base_params = base_params
        self.config = config or OptunaConfig()

    @staticmethod
    def _select_balanced_pareto_trial(
        best_trials: list,
        metrics: list,
        minimize_metrics: frozenset,
    ):
        """
        Select the most balanced Pareto-optimal trial using Euclidean distance
        to the ideal (Utopia) point across all normalized objectives.
        Only considers trials marked as valid.
        """
        valid_trials = [
            t
            for t in best_trials
            if t.user_attrs.get("valid", True) and t.values is not None
        ]
        if not valid_trials:
            return None
        if len(valid_trials) == 1:
            return valid_trials[0]

        n_metrics = len(metrics)
        metric_min = []
        metric_max = []
        for i in range(n_metrics):
            vals = [
                float(t.values[i])
                for t in valid_trials
                if t.values is not None
                and len(t.values) > i
                and math.isfinite(float(t.values[i]))
            ]
            if vals:
                metric_min.append(min(vals))
                metric_max.append(max(vals))
            else:
                metric_min.append(0.0)
                metric_max.append(1.0)

        best_trial = valid_trials[0]
        min_dist = float("inf")

        for t in valid_trials:
            if t.values is None or len(t.values) < n_metrics:
                continue
            dist_sq = 0.0
            for i, m in enumerate(metrics):
                raw_val = float(t.values[i])
                mn = metric_min[i]
                mx = metric_max[i]
                span = mx - mn
                if span > 1e-9:
                    if m in minimize_metrics:
                        norm = (mx - raw_val) / span
                    else:
                        norm = (raw_val - mn) / span
                else:
                    norm = 1.0
                norm = max(0.0, min(1.0, norm))
                dist_sq += (1.0 - norm) ** 2

            dist = math.sqrt(dist_sq)
            if dist < min_dist:
                min_dist = dist
                best_trial = t

        return best_trial

    def optimize(
        self, df: pd.DataFrame, is_cancelled: Any = None, progress_callback: Any = None
    ) -> dict[str, Any]:
        """Execute Optuna study to find optimal hyperparameter values."""
        # Support batch optimizer cancellation and progress callbacks
        if is_cancelled is not None and is_cancelled():
            return {"status": "cancelled", "message": "Optimization cancelled by user"}
        self._validate_config()
        if df.empty or len(df) < 50:
            raise ValueError(
                "Insufficient candle data provided for Optuna optimization (minimum 50 bars required)."
            )

        # Precompute shared indicators ONCE for the whole sweep (ema_vwap-jikw).
        # VWAP/slope/ATR/vol-SMA do not vary between trials; only the EMA periods
        # differ per candidate, and generate_signals adds those cheaply when
        # precomputed_indicators is provided.
        from src.indicators import compute_shared_indicators

        df_shared = compute_shared_indicators(df, self.base_params)

        startup_trials = self.config.n_startup_trials
        if startup_trials is None:
            startup_trials = min(25, max(5, int(self.config.n_trials * 0.1)))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sampler = optuna.samplers.TPESampler(
                multivariate=self.config.multivariate,
                group=self.config.multivariate,
                n_startup_trials=startup_trials,
                constant_liar=self.config.n_jobs != 1,
                seed=42,
                warn_independent_sampling=False,
            )

        # Determine optimization direction(s)
        if self.config.enable_multi_objective:
            # Multi-objective: determine direction for each metric
            directions = []
            for metric in self.config.multi_objective_metrics:
                # Minimize drawdown and ulcer; maximize others
                if metric in (
                    self._MINIMIZE_METRICS
                    if hasattr(self, "_MINIMIZE_METRICS")
                    else {"max_drawdown_pct", "ulcer_index"}
                ):
                    directions.append("minimize")
                else:
                    directions.append("maximize")
            study = optuna.create_study(directions=directions, sampler=sampler)
        else:
            # Single-objective: minimize if target is a "lower is better" metric
            minimize = self.config.target_metric in {"max_drawdown_pct", "ulcer_index"}
            study = optuna.create_study(
                direction="minimize" if minimize else "maximize", sampler=sampler
            )

        # Warm-start (ema_vwap-ddgr): if a previous sweep on the same combo
        # (strategy_mode + metric + EMA bounds) left trials in the in-memory cache,
        # add them to this fresh study so TPE starts from prior knowledge instead
        # of pure random exploration.
        warm_key = (
            str(getattr(self.base_params, "strategy_mode", "")),
            str(self.config.target_metric),
            tuple(sorted(self.config.multi_objective_metrics or [])),
            self.config.fast_ema_min,
            self.config.fast_ema_max,
            self.config.slow_ema_min,
            self.config.slow_ema_max,
        )
        prior_trials = (
            _optuna_warmstart_cache.pop(warm_key, [])
            if self.config.enable_warmstart
            else []
        )
        if prior_trials:
            for trial_params in prior_trials:
                try:
                    study.enqueue_trial(trial_params, skip_if_exists=True)
                except Exception as e:
                    logger.debug(f"[OptunaOptimizer] Warm-start enqueue skipped: {e}")
            logger.info(
                f"[OptunaOptimizer] Warm-started with {len(prior_trials)} prior trial(s)"
            )

        is_auto_mode = self.base_params.strategy_mode == "auto"
        fixed_mode = self.base_params.strategy_mode
        sl_type = self.base_params.stop_loss_type

        # Static delta search bounds for multivariate TPE stability
        slow_delta_min = max(2, self.config.slow_ema_min - self.config.fast_ema_min)
        slow_delta_max = max(
            slow_delta_min + 5, self.config.slow_ema_max - self.config.fast_ema_min
        )
        trend_delta_min = max(5, self.config.trend_ema_min - self.config.slow_ema_min)
        trend_delta_max = max(
            trend_delta_min + 10, self.config.trend_ema_max - self.config.slow_ema_min
        )

        # Enqueue baseline strategy parameters as Trial #0 if enabled
        if self.config.seed_base_params:
            base_trial_params: dict[str, Any] = {}
            if is_auto_mode:
                base_trial_params["strategy_mode"] = "crossover"

            base_trial_params["fast_ema"] = int(
                max(
                    self.config.fast_ema_min,
                    min(self.config.fast_ema_max, self.base_params.fast_ema),
                )
            )

            active_mode = "crossover" if is_auto_mode else fixed_mode
            if active_mode == "multi_ema":
                base_fast = base_trial_params["fast_ema"]
                base_slow_max = self.config.slow_ema_max - base_fast
                base_slow_delta = max(
                    2, self.base_params.slow_ema - self.base_params.fast_ema
                )
                base_slow_delta = min(base_slow_delta, base_slow_max)
                base_slow = base_fast + base_slow_delta
                base_trend_max = self.config.trend_ema_max - base_slow
                base_trend_delta = max(
                    5, self.base_params.trend_ema - self.base_params.slow_ema
                )
                base_trend_delta = min(base_trend_delta, base_trend_max)
                base_trial_params["slow_delta"] = int(
                    max(
                        slow_delta_min,
                        base_slow_delta,
                    )
                )
                base_trial_params["trend_delta"] = int(
                    max(
                        trend_delta_min,
                        base_trend_delta,
                    )
                )
            elif active_mode == "pullback":
                base_trial_params["pullback_tolerance_pct"] = float(
                    max(
                        self.config.pullback_tolerance_min,
                        min(
                            self.config.pullback_tolerance_max,
                            self.base_params.pullback_tolerance_pct,
                        ),
                    )
                )

            if sl_type == "vwap":
                base_trial_params["vwap_stop_offset_pct"] = float(
                    max(
                        self.config.vwap_stop_offset_min,
                        min(
                            self.config.vwap_stop_offset_max,
                            self.base_params.vwap_stop_offset_pct,
                        ),
                    )
                )
                base_trial_params["stop_loss_type"] = "vwap"
            else:
                base_trial_params["atr_multiplier"] = float(
                    max(
                        self.config.atr_multiplier_min,
                        min(
                            self.config.atr_multiplier_max,
                            self.base_params.atr_multiplier,
                        ),
                    )
                )
                base_trial_params["stop_loss_type"] = "atr"

            if self.base_params.volume_filter_enabled:
                base_trial_params["volume_multiplier"] = float(
                    max(
                        self.config.volume_multiplier_min,
                        min(
                            self.config.volume_multiplier_max,
                            self.base_params.volume_multiplier,
                        ),
                    )
                )

            base_trial_params["vwap_slope_min"] = float(
                max(
                    self.config.vwap_slope_min,
                    min(self.config.vwap_slope_max, self.base_params.vwap_slope_min),
                )
            )
            base_trial_params["risk_reward_ratio"] = float(
                max(
                    self.config.risk_reward_min,
                    min(
                        self.config.risk_reward_max, self.base_params.risk_reward_ratio
                    ),
                )
            )
            base_trial_params["risk_per_trade_pct"] = float(
                max(
                    self.config.risk_per_trade_min,
                    min(
                        self.config.risk_per_trade_max,
                        self.base_params.risk_per_trade_pct,
                    ),
                )
            )

            if (
                self.config.enable_max_holding_bars
                and self.base_params.max_holding_bars is not None
            ):
                base_trial_params["max_holding_bars"] = int(
                    max(
                        self.config.max_holding_bars_min,
                        min(
                            self.config.max_holding_bars_max,
                            self.base_params.max_holding_bars,
                        ),
                    )
                )

            try:
                study.enqueue_trial(base_trial_params)
            except Exception as e:
                logger.debug(f"[OptunaOptimizer] Base trial enqueue skipped: {e}")

        def objective(trial: optuna.Trial) -> float:
            if is_auto_mode:
                trial_mode = trial.suggest_categorical(
                    "strategy_mode", ["crossover", "multi_ema", "pullback"]
                )
            else:
                trial_mode = fixed_mode

            fast_ema = trial.suggest_int(
                "fast_ema", self.config.fast_ema_min, self.config.fast_ema_max
            )

            # Multi-EMA mode requires slow_ema and trend_ema
            if trial_mode == "multi_ema":
                slow_delta = trial.suggest_int(
                    "slow_delta", slow_delta_min, slow_delta_max
                )
                slow_ema = fast_ema + slow_delta

                trend_delta = trial.suggest_int(
                    "trend_delta", trend_delta_min, trend_delta_max
                )
                trend_ema = slow_ema + trend_delta

                if slow_ema > self.config.slow_ema_max:
                    raise optuna.TrialPruned("slow EMA exceeds configured maximum")
                if (
                    trend_ema < self.config.trend_ema_min
                    or trend_ema > self.config.trend_ema_max
                ):
                    raise optuna.TrialPruned("trend EMA is outside configured range")

                trial.set_user_attr("slow_ema", slow_ema)
                trial.set_user_attr("trend_ema", trend_ema)
            else:
                slow_ema = self.base_params.slow_ema
                trend_ema = self.base_params.trend_ema

            # Pullback mode requires pullback_tolerance_pct
            if trial_mode == "pullback":
                pullback_tol = trial.suggest_float(
                    "pullback_tolerance_pct",
                    self.config.pullback_tolerance_min,
                    self.config.pullback_tolerance_max,
                    step=0.05,
                )
            else:
                pullback_tol = self.base_params.pullback_tolerance_pct

            # Stop loss parameter tuning — randomly alternate between vwap and atr
            use_vwap_stop = trial.suggest_categorical("stop_loss_type", ["vwap", "atr"])
            if use_vwap_stop == "vwap":
                vwap_stop_offset = trial.suggest_float(
                    "vwap_stop_offset_pct",
                    self.config.vwap_stop_offset_min,
                    self.config.vwap_stop_offset_max,
                    step=0.02,
                )
                atr_multiplier = self.base_params.atr_multiplier
            else:
                vwap_stop_offset = self.base_params.vwap_stop_offset_pct
                atr_multiplier = trial.suggest_float(
                    "atr_multiplier",
                    self.config.atr_multiplier_min,
                    self.config.atr_multiplier_max,
                    step=0.1,
                )

            # Volume filter multiplier
            if self.base_params.volume_filter_enabled:
                volume_multiplier = trial.suggest_float(
                    "volume_multiplier",
                    self.config.volume_multiplier_min,
                    self.config.volume_multiplier_max,
                    step=0.1,
                )
            else:
                volume_multiplier = self.base_params.volume_multiplier

            if self.config.vwap_slope_min > 0:
                vwap_slope_min = trial.suggest_float(
                    "vwap_slope_min",
                    self.config.vwap_slope_min,
                    self.config.vwap_slope_max,
                    log=True,
                )
            else:
                vwap_slope_min = trial.suggest_float(
                    "vwap_slope_min",
                    self.config.vwap_slope_min,
                    self.config.vwap_slope_max,
                    step=0.0001,
                )

            risk_reward_ratio = trial.suggest_float(
                "risk_reward_ratio",
                self.config.risk_reward_min,
                self.config.risk_reward_max,
                step=0.1,
            )
            risk_per_trade_pct = trial.suggest_float(
                "risk_per_trade_pct",
                self.config.risk_per_trade_min,
                self.config.risk_per_trade_max,
                step=0.1,
            )

            # Max holding bars (optional sweep)
            if self.config.enable_max_holding_bars:
                max_holding_bars: int | None = trial.suggest_int(
                    "max_holding_bars",
                    self.config.max_holding_bars_min,
                    self.config.max_holding_bars_max,
                )
            else:
                max_holding_bars = None

            # Trailing stop % of price (profit lock)
            trailing_stop_pct = trial.suggest_float(
                "trailing_stop_pct",
                self.config.trailing_stop_min,
                self.config.trailing_stop_max,
                step=0.1,
            )

            # Max position cap as % of equity (portfolio-level)
            max_position_pct = trial.suggest_float(
                "max_position_pct",
                self.config.max_position_pct_min,
                self.config.max_position_pct_max,
                step=1.0,
            )

            # Build candidate params
            candidate_params = StrategyParams(
                strategy_mode=trial_mode,
                trade_direction=self.base_params.trade_direction,
                fast_ema=fast_ema,
                slow_ema=slow_ema,
                trend_ema=trend_ema,
                vwap_slope_min=vwap_slope_min,
                vwap_slope_lookback=self.base_params.vwap_slope_lookback,
                volume_filter_enabled=self.base_params.volume_filter_enabled,
                volume_multiplier=volume_multiplier,
                volume_sma_period=self.base_params.volume_sma_period,
                pullback_tolerance_pct=pullback_tol,
                stop_loss_type=use_vwap_stop,
                vwap_stop_offset_pct=vwap_stop_offset,
                atr_period=self.base_params.atr_period,
                atr_multiplier=atr_multiplier,
                risk_reward_ratio=risk_reward_ratio,
                risk_per_trade_pct=risk_per_trade_pct,
                max_position_pct=max_position_pct,
                max_holding_bars=max_holding_bars,
                trailing_stop_pct=trailing_stop_pct,
                initial_capital=self.base_params.initial_capital,
                maker_fee_pct=self.base_params.maker_fee_pct,
                taker_fee_pct=self.base_params.taker_fee_pct,
                slippage_pct=self.base_params.slippage_pct,
            )

            engine = BacktestEngine(candidate_params, precomputed_indicators=df_shared)
            result = engine.run(df)

            # Metrics that should be minimized (lower is better); all others maximized.
            _MINIMIZE_METRICS = frozenset({"max_drawdown_pct", "ulcer_index"})

            invalid_result: Any
            if self.config.enable_multi_objective:
                invalid_result = tuple(
                    float("inf") if m in _MINIMIZE_METRICS else float("-inf")
                    for m in self.config.multi_objective_metrics
                )
            else:
                invalid_result = float("-inf")

            if result.total_trades == 0:
                trial.set_user_attr("valid", False)
                return invalid_result

            # Enforce the minimum sample size rather than scaling the metric.
            if result.total_trades < self.config.min_trades:
                trial.set_user_attr("valid", False)
                return invalid_result

            if self.config.enable_multi_objective:
                # Validate every objective metric for NaN / non-finite before
                # building the return tuple — a single bad value corrupts Optuna's
                # Pareto dominance computation.
                sanitized: list = []
                any_invalid = False
                for m in self.config.multi_objective_metrics:
                    raw = getattr(result, m, None)
                    if raw is None or not isinstance(raw, (int, float)) or pd.isna(raw):
                        any_invalid = True
                        sanitized.append(
                            float("inf") if m in _MINIMIZE_METRICS else float("-inf")
                        )
                    else:
                        v = float(raw)
                        if not math.isfinite(v):
                            # e.g. profit_factor = inf when no losing trades
                            if m in _MINIMIZE_METRICS:
                                sanitized.append(float("inf"))
                            else:
                                # Cap at a large-but-finite sentinel so Pareto
                                # dominance comparisons remain meaningful.
                                sanitized.append(1e9 if v > 0 else float("-inf"))
                            any_invalid = True
                        else:
                            sanitized.append(v)
                if any_invalid:
                    trial.set_user_attr("valid", False)
                objectives = tuple(sanitized)
            else:
                # Single-objective: validate primary target metric.
                raw_metric = getattr(result, self.config.target_metric)
                if not isinstance(raw_metric, (int, float)) or pd.isna(raw_metric):
                    trial.set_user_attr("valid", False)
                    return invalid_result
                metric_val = float(raw_metric)
                if not math.isfinite(metric_val):
                    trial.set_user_attr("valid", False)
                    return invalid_result

            # Apply hard drawdown constraint if enabled
            if (
                self.config.enable_hard_drawdown_constraint
                and result.max_drawdown_pct > self.config.max_drawdown_constraint_pct
            ):
                trial.set_user_attr("valid", False)
                trial.set_user_attr("constraint_violated", "max_drawdown")
                raise optuna.TrialPruned(
                    f"Max drawdown {result.max_drawdown_pct:.2f}% exceeds "
                    f"hard constraint {self.config.max_drawdown_constraint_pct:.2f}%"
                )

            # Return objectives
            if self.config.enable_multi_objective:
                return objectives
            else:
                return metric_val

        study.optimize(
            objective,
            n_trials=self.config.n_trials,
            timeout=self.config.timeout_seconds,
            n_jobs=self.config.n_jobs,
        )

        # Warm-start bookkeeping (ema_vwap-ddgr): stash the top finished trials'
        # params so the next sweep on the same combo starts from them.
        # Only when enable_warmstart is set — default off preserves the
        # seed-reproducibility contract of repeated sweeps.
        try:
            if not self.config.enable_warmstart:
                raise RuntimeError("warm-start disabled")  # jump to except: pass
            if self.config.enable_multi_objective:
                ranked = sorted(
                    study.best_trials,
                    key=lambda t: sum(
                        (v if isinstance(v, (int, float)) else 0)
                        for v in (t.values or [])
                    ),
                    reverse=True,
                )[:_warmstart_max_trials]
            else:
                ranked = sorted(
                    study.trials,
                    key=lambda t: (
                        -(
                            t.value
                            if isinstance(t.value, (int, float))
                            else float("-inf")
                        )
                    ),
                )[:_warmstart_max_trials]
            _optuna_warmstart_cache[warm_key] = [dict(t.params) for t in ranked]
        except Exception as e:
            logger.debug(f"[OptunaOptimizer] Warm-start cache save skipped: {e}")

        # Handle best params: single-objective vs multi-objective
        if self.config.enable_multi_objective:
            # Multi-objective: pick balanced Pareto-optimal trial via Utopia point distance
            best_trial = self._select_balanced_pareto_trial(
                study.best_trials,
                self.config.multi_objective_metrics,
                self._MINIMIZE_METRICS,
            )
            if best_trial is None:
                # All trials were invalid or non-Pareto-optimal; return safe null result
                result_dict = {
                    "target_metric": self.config.target_metric,
                    "best_params": self.base_params.to_dict(),
                    "param_importance": {},
                    "top_trials": [],
                    "best_backtest_result": {},
                    "reproducible": True,
                    "parallelism_mode": self.config.execution_mode,
                    "optimization_mode": "multi_objective",
                    "pareto_objectives": self.config.multi_objective_metrics,
                    "num_pareto_trials": 0,
                    "selected_pareto_trial": None,
                    "best_value": None,
                    "best_value_valid": False,
                    "best_metric_value": None,
                    "pareto_frontier": [],
                }
                return result_dict
            best_params_dict = self.base_params.to_dict()
            best_params_dict.update(best_trial.params)
        else:
            # Single-objective: use best_params
            best_params_dict = self.base_params.to_dict()
            best_params_dict.update(study.best_params)

        if "slow_delta" in best_params_dict:
            fast_val = best_params_dict.get("fast_ema", self.base_params.fast_ema)
            best_params_dict["slow_ema"] = fast_val + best_params_dict.pop("slow_delta")
        if "trend_delta" in best_params_dict:
            slow_val = best_params_dict.get("slow_ema", self.base_params.slow_ema)
            best_params_dict["trend_ema"] = slow_val + best_params_dict.pop(
                "trend_delta"
            )

        # Calculate parameter importance (only for single-objective)
        import warnings as _warnings

        try:
            if not self.config.enable_multi_objective:
                with _warnings.catch_warnings():
                    _warnings.filterwarnings(
                        "ignore", message=".*have less than 2 trials.*"
                    )
                    param_importance = optuna.importance.get_param_importances(study)
            else:
                param_importance = {}
        except Exception:
            param_importance = {}

        # Collect top trials
        trials_list = []
        for t in study.trials:
            trial_values = t.values if self.config.enable_multi_objective else t.value
            if trial_values is not None:
                # For multi-objective, skip invalid trials
                if self.config.enable_multi_objective and not t.user_attrs.get(
                    "valid", True
                ):
                    continue
                p = dict(t.params)
                if "slow_delta" in p:
                    fast_v = p.get("fast_ema", self.base_params.fast_ema)
                    p["slow_ema"] = fast_v + p["slow_delta"]
                if "trend_delta" in p:
                    slow_v = p.get("slow_ema", self.base_params.slow_ema)
                    p["trend_ema"] = slow_v + p["trend_delta"]

                if self.config.enable_multi_objective:
                    value_dict = {
                        metric: round(float(v), 4)
                        for metric, v in zip(
                            self.config.multi_objective_metrics, trial_values
                        )
                    }
                    trials_list.append(
                        {
                            "trial_number": t.number,
                            "value": value_dict,
                            "params": p,
                        }
                    )
                else:
                    trials_list.append(
                        {
                            "trial_number": t.number,
                            "value": (
                                round(float(trial_values), 4)
                                if math.isfinite(float(trial_values))
                                else None
                            ),
                            "params": p,
                        }
                    )

        trials_list.sort(
            key=lambda x: (
                next(iter(x["value"].values()))
                if isinstance(x["value"], dict)
                else (x["value"] if x["value"] is not None else float("-inf"))
            ),
            reverse=(
                not self.config.enable_multi_objective
                and self.config.target_metric not in {"max_drawdown_pct", "ulcer_index"}
            ),
        )

        # Run final backtest with best parameters
        best_strategy_params = StrategyParams(**best_params_dict)
        final_result = BacktestEngine(best_strategy_params).run(df)

        # Prepare return dict
        result_dict = {
            "target_metric": self.config.target_metric,
            "best_params": best_strategy_params.to_dict(),
            "param_importance": {k: round(v, 4) for k, v in param_importance.items()},
            "top_trials": trials_list[:15],
            "best_backtest_result": final_result.to_dict(),
            "reproducible": True,
            "parallelism_mode": self.config.execution_mode,
        }

        # Add single-objective or multi-objective specific fields
        if self.config.enable_multi_objective:
            result_dict["optimization_mode"] = "multi_objective"
            result_dict["pareto_objectives"] = self.config.multi_objective_metrics
            result_dict["num_pareto_trials"] = len(study.best_trials)
            result_dict["selected_pareto_trial"] = best_trial.number
            primary_metric = self.config.multi_objective_metrics[0]
            val = round(float(getattr(final_result, primary_metric)), 4)
            result_dict["best_value"] = val
            result_dict["best_value_valid"] = True
            # Add Pareto frontier details
            pareto_details = []
            for trial in study.best_trials[:10]:  # Top 10 Pareto trials
                p = dict(trial.params)
                if "slow_delta" in p:
                    fast_v = p.get("fast_ema", self.base_params.fast_ema)
                    p["slow_ema"] = fast_v + p["slow_delta"]
                if "trend_delta" in p:
                    slow_v = p.get("slow_ema", self.base_params.slow_ema)
                    p["trend_ema"] = slow_v + p["trend_delta"]

                pareto_details.append(
                    {
                        "trial_number": trial.number,
                        "objectives": {
                            metric: round(float(v), 4)
                            for metric, v in zip(
                                self.config.multi_objective_metrics, trial.values
                            )
                        },
                        "params": p,
                    }
                )
            result_dict["pareto_frontier"] = pareto_details
        else:
            result_dict["optimization_mode"] = "single_objective"
            result_dict["best_value"] = (
                round(float(study.best_value), 4)
                if math.isfinite(study.best_value)
                else None
            )
            result_dict["best_value_valid"] = math.isfinite(study.best_value)

        if self.config.enable_multi_objective:
            primary_metric = self.config.multi_objective_metrics[0]
            result_dict["best_metric_value"] = round(
                float(getattr(final_result, primary_metric)), 4
            )
        else:
            result_dict["best_metric_value"] = round(
                float(getattr(final_result, self.config.target_metric)), 4
            )

        return result_dict

    def _validate_config(self) -> None:
        """Reject invalid optimization settings before creating an Optuna study."""
        config = self.config
        if config.target_metric not in self._TARGET_METRICS:
            raise ValueError(
                f"Unsupported Optuna target metric: {config.target_metric}"
            )
        if config.n_trials < 1:
            raise ValueError("n_trials must be at least 1")
        if config.min_trades < 0:
            raise ValueError("min_trades must be non-negative")
        if config.n_jobs == 0:
            raise ValueError("n_jobs cannot be 0")
        if config.enable_multi_objective and not config.multi_objective_metrics:
            raise ValueError(
                "multi_objective_metrics must be a non-empty list when "
                "enable_multi_objective is True"
            )
        if config.enable_multi_objective:
            seen = set()
            for m in config.multi_objective_metrics:
                if m not in self._TARGET_METRICS:
                    raise ValueError(
                        f"Invalid multi-objective metric: {m}. "
                        f"Supported: {sorted(self._TARGET_METRICS)}"
                    )
                if m in seen:
                    raise ValueError(f"Duplicate multi-objective metric: {m}")
                seen.add(m)

        # Check for infeasible EMA ranges when using multi_ema strategy mode
        if self.base_params.strategy_mode == "multi_ema":
            # Check if fast_min can be less than slow_min, and slow_min can be less than trend_min
            # For any valid: fast < slow < trend, we need: fast_min + (slow_min - fast_min) < slow_max
            # and slow_min + (trend_min - slow_min) < trend_max
            # Simplified: slow_ema_max must be > fast_ema_min, and trend_ema_max > slow_ema_min
            if config.slow_ema_max <= config.fast_ema_min:
                raise ValueError("No feasible fast < slow < trend EMA range")
            if config.trend_ema_max <= config.slow_ema_min:
                raise ValueError("No feasible fast < slow < trend EMA range")

        ranges = (
            ("fast_ema", config.fast_ema_min, config.fast_ema_max),
            ("slow_ema", config.slow_ema_min, config.slow_ema_max),
            ("trend_ema", config.trend_ema_min, config.trend_ema_max),
            ("vwap_slope", config.vwap_slope_min, config.vwap_slope_max),
            (
                "volume_multiplier",
                config.volume_multiplier_min,
                config.volume_multiplier_max,
            ),
            ("atr_multiplier", config.atr_multiplier_min, config.atr_multiplier_max),
            ("risk_reward", config.risk_reward_min, config.risk_reward_max),
            (
                "pullback_tolerance",
                config.pullback_tolerance_min,
                config.pullback_tolerance_max,
            ),
            (
                "vwap_stop_offset",
                config.vwap_stop_offset_min,
                config.vwap_stop_offset_max,
            ),
            ("risk_per_trade", config.risk_per_trade_min, config.risk_per_trade_max),
        )
        for name, minimum, maximum in ranges:
            if minimum > maximum:
                raise ValueError(f"Invalid {name} range: minimum exceeds maximum")

    @staticmethod
    def _get_relational_ema_search_bounds(config):
        """Calculate the valid search bounds for relational EMA parameters."""
        slow_delta_min = max(2, config.slow_ema_min - config.fast_ema_min)
        trend_delta_min = max(5, config.trend_ema_min - config.slow_ema_min)
        return {
            "fast_ema_min": config.fast_ema_min,
            "fast_ema_max": config.fast_ema_max,
            "slow_delta_min": slow_delta_min,
            "slow_delta_max": max(
                slow_delta_min + 5, config.slow_ema_max - config.fast_ema_min
            ),
            "trend_delta_min": trend_delta_min,
            "trend_delta_max": max(
                trend_delta_min + 10, config.trend_ema_max - config.slow_ema_min
            ),
        }


TARGET_METRICS = OptunaOptimizer._TARGET_METRICS


# -------------------------------------------------------------------
# Optuna framework classes (previously deleted, now restored)
# -------------------------------------------------------------------
class OptunaTrialSpec:
    """Optuna trial specification and parameter configuration for EMA/VWAP optimization."""

    @classmethod
    def create(cls, base_params, config):
        """Create trial specification from base strategy parameters and Optuna config."""
        return cls(base_params, config)

    def __init__(self, base_params, config):
        self.base_params = base_params
        self.config = config
        # Set default auto_mode and fast_ema_max based on strategy_mode
        if hasattr(base_params, "strategy_mode"):
            if base_params.strategy_mode == "crossover":
                self.is_auto_mode = True
                self.fast_ema_max = 30
            else:
                self.is_auto_mode = False
                self.fast_ema_max = 20
        else:
            self.is_auto_mode = False
            self.fast_ema_max = 20


class OptunaObjectiveEvaluator:
    """Objective evaluator for Optuna hyperparameter optimization with picklability.

    ema_vwap-gm0i: the candle payload is stored as a compact columnar tuple
    (cheap to pickle across spawn-based worker processes on Windows) and the
    full DataFrame is rebuilt lazily on first use in the worker. A pickled
    DataFrame drags index blocks and manager objects; columnar tuples do not.
    """

    def __init__(self, spec, df):
        self.spec = spec
        self._df_cache = None
        if isinstance(df, pd.DataFrame):
            self._columns = tuple(df.columns)
            self._data = tuple(
                tuple(row) for row in df.itertuples(index=False, name=None)
            )
        else:
            # Already compact or non-DataFrame input: pass through unchanged.
            self._columns = None
            self._data = df

    @property
    def df(self) -> pd.DataFrame:
        """Rebuild (and cache) the DataFrame from the compact columnar payload."""
        if self._df_cache is None:
            if self._columns is not None:
                self._df_cache = pd.DataFrame(
                    list(self._data), columns=list(self._columns)
                )
            else:
                self._df_cache = self._data
        return self._df_cache

    def __call__(self, trial):
        """Optuna objective function for hyperparameter optimization."""
        base_params = self.spec.base_params
        config = self.spec.config
        is_auto_mode = self.spec.is_auto_mode

        # Determine strategy mode based on auto mode flag
        if is_auto_mode:
            trial_mode = trial.suggest_categorical(
                "strategy_mode", ["crossover", "multi_ema", "pullback"]
            )
        else:
            trial_mode = base_params.strategy_mode

        # Sample fast_ema parameter
        fast_ema = trial.suggest_int(
            "fast_ema", config.fast_ema_min, config.fast_ema_max
        )

        # Handle multi_ema mode with slow_ema and trend_ema
        if trial_mode == "multi_ema":
            slow_delta = trial.suggest_int("slow_delta", 5, 50)
            slow_ema = fast_ema + slow_delta
            trend_delta = trial.suggest_int("trend_delta", 50, 150)
            trend_ema = slow_ema + trend_delta

            if slow_ema > config.slow_ema_max:
                raise optuna.TrialPruned("slow EMA exceeds configured maximum")
            if trend_ema < config.trend_ema_min or trend_ema > config.trend_ema_max:
                raise optuna.TrialPruned("trend EMA outside configured range")

            params = StrategyParams(
                strategy_mode=trial_mode,
                fast_ema=fast_ema,
                slow_ema=slow_ema,
                trend_ema=trend_ema,
                vwap_slope_min=config.vwap_slope_min,
                atr_multiplier=trial.suggest_float(
                    "atr_multiplier",
                    config.atr_multiplier_min,
                    config.atr_multiplier_max,
                ),
            )
        else:
            # For non-multi_ema modes
            trend_ema = trial.suggest_int(
                "trend_ema", config.trend_ema_min, config.trend_ema_max
            )
            params = StrategyParams(
                strategy_mode=trial_mode,
                fast_ema=fast_ema,
                trend_ema=trend_ema,
                vwap_slope_min=config.vwap_slope_min,
                atr_multiplier=trial.suggest_float(
                    "atr_multiplier",
                    config.atr_multiplier_min,
                    config.atr_multiplier_max,
                ),
            )

        # Merge base_params with sampled params (including defaults for other fields)
        merged_params = StrategyParams(
            strategy_mode=params.strategy_mode,
            trade_direction=base_params.trade_direction,
            fast_ema=params.fast_ema,
            slow_ema=(
                params.slow_ema if hasattr(params, "slow_ema") else base_params.slow_ema
            ),
            trend_ema=params.trend_ema,
            vwap_slope_min=base_params.vwap_slope_min,
            vwap_slope_lookback=base_params.vwap_slope_lookback,
            volume_filter_enabled=base_params.volume_filter_enabled,
            volume_multiplier=base_params.volume_multiplier,
            volume_sma_period=base_params.volume_sma_period,
            pullback_tolerance_pct=base_params.pullback_tolerance_pct,
            stop_loss_type=base_params.stop_loss_type,
            vwap_stop_offset_pct=base_params.vwap_stop_offset_pct,
            atr_period=base_params.atr_period,
            atr_multiplier=params.atr_multiplier,
            risk_reward_ratio=base_params.risk_reward_ratio,
            risk_per_trade_pct=base_params.risk_per_trade_pct,
            max_holding_bars=base_params.max_holding_bars,
            trailing_stop_pct=base_params.trailing_stop_pct,
            initial_capital=base_params.initial_capital,
            maker_fee_pct=base_params.maker_fee_pct,
            taker_fee_pct=base_params.taker_fee_pct,
            slippage_pct=base_params.slippage_pct,
            asset_type=base_params.asset_type,
            session_mode=base_params.session_mode,
            vwap_anchor=base_params.vwap_anchor,
            allow_fractional_shares=base_params.allow_fractional_shares,
            enforce_margin_calls=base_params.enforce_margin_calls,
            min_margin_equity=base_params.min_margin_equity,
            long_buying_power_ratio=base_params.long_buying_power_ratio,
            short_buying_power_ratio=base_params.short_buying_power_ratio,
            sec_fee_per_million=base_params.sec_fee_per_million,
            finra_taf_per_share=base_params.finra_taf_per_share,
            position_size=base_params.position_size,
        )

        # Run backtest and get results
        engine = BacktestEngine(merged_params)
        try:
            results = engine.run(self.df)
        except Exception as e:
            raise ValueError(f"BacktestEngine.run failed: {e}")

        # For single-objective optimization, return the target metric
        target_metric = config.target_metric

        if not hasattr(results, target_metric):
            raise ValueError(f"Results missing target metric: {target_metric}")

        metric_value = getattr(results, target_metric)

        # Validate metric value
        if (
            metric_value is None
            or not isinstance(metric_value, (int, float))
            or pd.isna(metric_value)
        ):
            trial.set_user_attr("valid", False)
            raise optuna.TrialPruned(f"Invalid metric value: {metric_value}")

        metric_val = float(metric_value)
        if not math.isfinite(metric_val):
            trial.set_user_attr("valid", False)
            raise optuna.TrialPruned(f"Non-finite metric value: {metric_val}")

        # Check minimum trades requirement
        if results.total_trades < config.min_trades:
            trial.set_user_attr("valid", False)
            raise optuna.TrialPruned(
                f"Insufficient trades: {results.total_trades} < {config.min_trades}"
            )

        # Apply hard drawdown constraint if configured
        if (
            config.enable_hard_drawdown_constraint
            and hasattr(results, "max_drawdown_pct")
            and results.max_drawdown_pct > config.max_drawdown_constraint_pct
        ):
            trial.set_user_attr("valid", False)
            trial.set_user_attr("constraint_violated", "max_drawdown")
            raise optuna.TrialPruned(
                f"Max drawdown {results.max_drawdown_pct:.2f}% exceeds "
                f"hard constraint {config.max_drawdown_constraint_pct:.2f}%"
            )

        # Mark trial as valid if we reach here
        trial.set_user_attr("valid", True)

        return metric_val


def run_worker_trials(evaluator, total_trials=10, n_workers=1, master_seed=None):
    """Run worker trials with deterministic seed allocation for parallel execution."""
    import optuna

    if master_seed is not None:
        optuna.set_search_space_seed(master_seed)

    study = optuna.create_study(direction="maximize")
    study.optimize(evaluator, n_trials=total_trials)
    return study


class OptunaStudyCoordinator:
    """Manages Optuna studies with journal storage and multi-process coordination for Windows compatibility (local filesystem storage)."""

    def __init__(self, journal_path=None, persist_journal=False):
        import tempfile
        from pathlib import Path

        self.persist_journal = persist_journal
        if journal_path is None:
            journal_path = Path(tempfile.mktemp(suffix=".journal"))
            self._temp_journal = True
        else:
            journal_path = Path(journal_path)
            self._temp_journal = False

        self.journal_path = str(journal_path)
        self.study_name = f"study_{hash(self.journal_path)}"
        self._storage = self._create_storage()
        self._study = None

    @staticmethod
    def _get_file_lock(path: str):
        """Return a Windows-safe file lock for Optuna journal storage.

        The default JournalFileSymlinkLock uses os.symlink() which requires
        SeCreateSymbolicLinkPrivilege on Windows — not available to normal
        users.  JournalFileOpenLock uses open() and works everywhere.
        """
        import sys

        from optuna.storages.journal._file import (
            JournalFileOpenLock,
            JournalFileSymlinkLock,
        )

        if sys.platform == "win32":
            return JournalFileOpenLock(path)
        return JournalFileSymlinkLock(path)

    def _create_storage(self):
        """Create journal storage with file locking for local filesystem."""
        from optuna.storages import JournalStorage
        from optuna.storages.journal import JournalFileBackend

        lock = self._get_file_lock(self.journal_path)
        return JournalStorage(JournalFileBackend(self.journal_path, lock_obj=lock))

    @staticmethod
    def allocate_trial_budgets(total_trials, n_workers):
        """Partition total_trials across n_workers with zero drift (balanced allocation)."""
        if n_workers <= 0:
            raise ValueError("n_workers must be positive")
        base = total_trials // n_workers
        remainder = total_trials % n_workers
        return [base + (1 if i < remainder else 0) for i in range(n_workers)]

    @staticmethod
    def allocate_worker_seeds(master_seed, n_workers):
        """Deterministic, distinct sampler seeds per worker."""
        if master_seed is None:
            return [None] * n_workers
        return [master_seed + i for i in range(n_workers)]

    def create_study(self, directions="maximize"):
        """Create new Optuna study with specified directions."""
        import optuna

        # Create study with journal storage
        study = optuna.create_study(
            storage=self._storage,
            study_name=self.study_name,
            direction=directions,
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        self._study = study
        return study

    def run_workers(
        self,
        evaluator,
        total_trials=10,
        n_workers=1,
        master_seed=None,
        timeout_seconds=None,
    ):
        """Run trials across multiple worker processes with exact trial accounting.

        Args:
            evaluator: Callable that takes an optuna.Trial and returns a metric value.
            total_trials: Total number of trials to distribute across workers.
            n_workers: Number of parallel worker processes.
            master_seed: Base seed for deterministic seed allocation per worker.
            timeout_seconds: If set, cancel workers after this many seconds and
                return partial results. If no worker exceptions occurred, a
                merged study with whatever trials completed is returned.

        Returns:
            A merged Optuna study containing all completed trials.

        Raises:
            Exception: If any worker process raises an exception, it is re-raised
                after cleanup of temporary journal files.
        """
        from concurrent.futures import ProcessPoolExecutor, wait
        from pathlib import Path

        import optuna

        # Allocate trial budgets
        budgets = self.allocate_trial_budgets(total_trials, n_workers)

        # Create per-worker studies with isolated storage
        studies = []
        for i, budget in enumerate(budgets):
            worker_seed = master_seed + i if master_seed is not None else None
            worker_journal = Path(self.journal_path).with_suffix(f".worker{i}.journal")
            worker_storage = self._create_worker_storage(worker_journal)

            worker_study = optuna.create_study(
                storage=worker_storage,
                study_name=f"{self.study_name}_worker{i}",
                direction="maximize",
                sampler=optuna.samplers.TPESampler(seed=worker_seed),
            )
            studies.append(worker_study)

        # Execute trials in parallel
        completed_studies = []
        futures = []
        errors = []
        timed_out = False

        executor = ProcessPoolExecutor(max_workers=n_workers)
        try:
            for i, (budget, study) in enumerate(zip(budgets, studies)):
                worker_seed = master_seed + i if master_seed is not None else None
                future = executor.submit(
                    self._run_worker_trials, evaluator, budget, worker_seed, study
                )
                futures.append(future)

            if timeout_seconds is not None:
                done, not_done = wait(futures, timeout=timeout_seconds)
                timed_out = len(not_done) > 0
            else:
                done, not_done = set(futures), set()
                timed_out = False

            for future in done:
                try:
                    completed_studies.append(future.result())
                except Exception as e:
                    errors.append(e)
        finally:
            # If timed out, force shutdown without waiting for running processes
            if timed_out:
                for future in not_done:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        # Propagate any worker exceptions after cleanup
        if errors:
            self.cleanup()
            raise errors[0]

        # Combine results from all workers
        combined_study = self._merge_studies(completed_studies)
        return combined_study

    def _create_worker_storage(self, journal_path):
        """Create worker-specific storage for isolated execution."""
        from optuna.storages import JournalStorage
        from optuna.storages.journal import JournalFileBackend

        path_str = str(journal_path)
        lock = self._get_file_lock(path_str)
        return JournalStorage(JournalFileBackend(path_str, lock_obj=lock))

    def _run_worker_trials(self, evaluator, budget, seed, study):
        """Internal method to run trials for a worker."""
        import optuna

        if seed is not None:
            study.sampler = optuna.samplers.TPESampler(seed=seed)
        study.optimize(evaluator, n_trials=budget)
        return study

    def _merge_studies(self, studies):
        """Merge multiple studies into one consolidated study."""
        import optuna

        # Create a new study and add trials from all worker studies
        merged_study = optuna.create_study(direction="maximize")
        for study in studies:
            for trial in study.get_trials():
                merged_study.add_trial(trial)
        return merged_study

    def get_study(self):
        """Get the current study instance."""
        return self._study

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup temporary files."""
        self.cleanup()

    def cleanup(self):
        """Remove temporary journal files and lock files for this coordinator."""
        import glob
        import os

        self._study = None
        if not (hasattr(self, "_temp_journal") and self._temp_journal):
            return

        if os.path.exists(self.journal_path):
            try:
                os.unlink(self.journal_path)
            except OSError:
                pass
        lock_path = f"{self.journal_path}.lock"
        if os.path.exists(lock_path):
            try:
                os.unlink(lock_path)
            except OSError:
                pass

        journal_dir = os.path.dirname(self.journal_path) or "."
        journal_name = os.path.basename(self.journal_path)
        journal_stem = journal_name.removesuffix(".journal")
        for worker_file in glob.glob(
            os.path.join(journal_dir, f"{journal_stem}.worker*.journal*")
        ):
            try:
                os.unlink(worker_file)
            except OSError:
                pass


def create_journal_storage(journal_path):
    """Create journal storage for Optuna studies with local filesystem constraint."""
    import sys

    from optuna.storages import JournalStorage
    from optuna.storages.journal import JournalFileBackend
    from optuna.storages.journal._file import (
        JournalFileOpenLock,
        JournalFileSymlinkLock,
    )

    lock = (
        JournalFileOpenLock(journal_path)
        if sys.platform == "win32"
        else JournalFileSymlinkLock(journal_path)
    )
    return JournalStorage(JournalFileBackend(journal_path, lock_obj=lock))
