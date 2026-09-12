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

# Suppress verbose Optuna logging during web API calls
optuna.logging.set_verbosity(optuna.logging.WARNING)
warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)


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
        """
        if not best_trials:
            return None
        if len(best_trials) == 1:
            return best_trials[0]

        n_metrics = len(metrics)
        metric_min = []
        metric_max = []
        for i in range(n_metrics):
            vals = [
                float(t.values[i])
                for t in best_trials
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

        best_trial = best_trials[0]
        min_dist = float("inf")

        for t in best_trials:
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

    def optimize(self, df: pd.DataFrame) -> dict[str, Any]:
        """Execute Optuna study to find optimal hyperparameter values."""
        self._validate_config()
        if df.empty or len(df) < 50:
            raise ValueError(
                "Insufficient candle data provided for Optuna optimization (minimum 50 bars required)."
            )

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
                if metric in ("max_drawdown_pct", "ulcer_index"):
                    directions.append("minimize")
                else:
                    directions.append("maximize")
            study = optuna.create_study(directions=directions, sampler=sampler)
        else:
            # Single-objective
            study = optuna.create_study(direction="maximize", sampler=sampler)

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
            except Exception:
                pass

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

            # Stop loss parameter tuning
            if sl_type == "vwap":
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
                stop_loss_type=self.base_params.stop_loss_type,
                vwap_stop_offset_pct=vwap_stop_offset,
                atr_period=self.base_params.atr_period,
                atr_multiplier=atr_multiplier,
                risk_reward_ratio=risk_reward_ratio,
                risk_per_trade_pct=risk_per_trade_pct,
                max_holding_bars=max_holding_bars,
                initial_capital=self.base_params.initial_capital,
                maker_fee_pct=self.base_params.maker_fee_pct,
                taker_fee_pct=self.base_params.taker_fee_pct,
                slippage_pct=self.base_params.slippage_pct,
            )

            engine = BacktestEngine(candidate_params)
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
            if self.config.enable_hard_drawdown_constraint:
                if result.max_drawdown_pct > self.config.max_drawdown_constraint_pct:
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

        # Handle best params: single-objective vs multi-objective
        if self.config.enable_multi_objective:
            # Multi-objective: pick balanced Pareto-optimal trial via Utopia point distance
            best_trial = self._select_balanced_pareto_trial(
                study.best_trials,
                self.config.multi_objective_metrics,
                self._MINIMIZE_METRICS,
            )
            if best_trial is None:
                raise ValueError("No valid trials found in Pareto frontier")
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
        try:
            if not self.config.enable_multi_objective:
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
            reverse=True,
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
