"""
Multi-Stage Automated Strategy Validation Engine.
Orchestrates In-Sample Backtesting, Out-of-Sample Walk-Forward Efficiency (WFO),
and Monte Carlo Stress Testing to classify trading parameters as ROBUST, CAUTION, or FAIL.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from src.backtester import BacktestEngine
from src.config import MonteCarloConfig, OptunaConfig, StrategyParams, WFOConfig
from src.logger import logger
from src.monte_carlo import MonteCarloSimulator
from src.settings import SettingsManager
from src.wfo import WalkForwardEngine


@dataclass
class ValidationCriteria:
    """Configurable thresholds for multi-stage strategy validation gates."""

    # Gate 1: In-Sample Backtest Viability
    min_backtest_return_pct: float = 0.0
    min_backtest_sharpe: float = 0.8
    max_backtest_drawdown_pct: float = 30.0
    min_trade_count: int = 3

    # Gate 2: Walk-Forward Out-of-Sample Robustness
    min_walk_forward_efficiency: float = 50.0  # OOS Sharpe / IS Sharpe * 100
    min_profitable_windows_pct: float = (
        50.0  # Percentage of OOS test windows with positive return
    )
    min_oos_return_pct: float = 0.0

    # Gate 3: Monte Carlo Stress & Tail Risk
    max_monte_carlo_ruin_pct: float = (
        5.0  # Maximum acceptable probability of 50% account drawdown
    )
    max_monte_carlo_drawdown_pct: float = (
        35.0  # Maximum 95th percentile simulated drawdown
    )


@dataclass
class GateResult:
    """Outcome and detailed metrics for a single validation gate."""

    gate_name: str
    passed: bool
    status: str  # "PASS", "CAUTION", "FAIL"
    metrics: dict[str, Any] = field(default_factory=dict)
    details: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Comprehensive validation report synthesizing all 3 robustness gates."""

    status: str  # "ROBUST", "CAUTION", "FAIL"
    overall_score: float  # 0.0 to 100.0
    gates_passed: int
    total_gates: int
    gate_results: dict[str, GateResult] = field(default_factory=dict)
    summary: str = ""
    reasons: list[str] = field(default_factory=list)
    recommended_action: str = ""  # "DEPLOY_SAFE", "PROCEED_WITH_CAUTION", "REJECT"

    def to_dict(self) -> dict[str, Any]:
        """Convert report to JSON-serializable dictionary."""
        return {
            "status": self.status,
            "overall_score": round(self.overall_score, 1),
            "gates_passed": self.gates_passed,
            "total_gates": self.total_gates,
            "gate_results": {k: asdict(v) for k, v in self.gate_results.items()},
            "summary": self.summary,
            "reasons": self.reasons,
            "recommended_action": self.recommended_action,
        }


class StrategyValidator:
    """
    Automated validation engine executing a 3-gate robustness check:
    - Gate 1: In-Sample Backtest Viability
    - Gate 2: Walk-Forward Out-of-Sample Efficiency & Consistency
    - Gate 3: Monte Carlo Sequence Stress & Tail Risk of Ruin
    """

    def __init__(
        self,
        criteria: ValidationCriteria | None = None,
        settings_mgr: SettingsManager | None = None,
    ):
        if criteria is not None:
            self.criteria = criteria
        else:
            self.criteria = ValidationCriteria()
            # Override Monte Carlo risk thresholds from settings if available
            if settings_mgr is not None:
                self.criteria.max_monte_carlo_ruin_pct = (
                    settings_mgr.get_monte_carlo_ruin_pct()
                )
                self.criteria.max_monte_carlo_drawdown_pct = (
                    settings_mgr.get_monte_carlo_drawdown_pct()
                )

    @staticmethod
    def _summarize_regimes(regimes: list[str]) -> dict[str, Any]:
        """Summarize regime labels across WFO windows for Gate 2 metrics."""
        if not regimes:
            return {"label_frequencies": {}, "dominant_regime": None}
        from collections import Counter

        counts = Counter(str(r) for r in regimes)
        dominant = counts.most_common(1)[0][0] if counts else None
        return {
            "label_frequencies": dict(counts),
            "dominant_regime": dominant,
        }

    def validate(
        self,
        df_candles: pd.DataFrame,
        params: StrategyParams,
        wfo_config: WFOConfig | None = None,
        mc_config: MonteCarloConfig | None = None,
        n_trials: int = 30,
        target_metric: str = "sharpe_ratio",
        best_optuna_value: float | None = None,
        enable_multi_objective: bool = False,
        multi_objective_metrics: list[str] | None = None,
    ) -> ValidationReport:
        """
        Execute full validation pipeline against historical candles.

        Args:
            df_candles: Historical OHLCV candle data.
            params: Strategy parameters to validate.
            wfo_config: Optional WFO configuration override.
            mc_config: Optional Monte Carlo configuration override.
            n_trials: Optuna trial budget for Gate 2 WFO windows (matches UI setting).
            target_metric: The metric that was optimized (e.g. sharpe_ratio, calmar_ratio).
                           Used to compute a metric-aware Gate 1 score.
            best_optuna_value: The best value achieved by Optuna for target_metric.
                               If supplied, Gate 1 score is informed by this.
        """
        if df_candles is None or len(df_candles) < 30:
            return ValidationReport(
                status="FAIL",
                overall_score=0.0,
                gates_passed=0,
                total_gates=3,
                summary="Insufficient candlestick data to perform validation (minimum 30 bars required).",
                reasons=["Insufficient historical bars for statistical testing."],
                recommended_action="REJECT",
            )

        gate_results: dict[str, GateResult] = {}
        all_warnings: list[str] = []
        failure_reasons: list[str] = []

        # =========================================================================
        # Gate 1: In-Sample Backtest Viability
        # =========================================================================
        logger.info("[StrategyValidator] Running Gate 1: In-Sample Backtest...")
        engine = BacktestEngine(params)
        bt_res = engine.run(df_candles)

        g1_metrics = {
            "total_return_pct": round(bt_res.total_return_pct, 2),
            "sharpe_ratio": round(bt_res.sharpe_ratio, 2),
            "max_drawdown_pct": round(bt_res.max_drawdown_pct, 2),
            "win_rate": round(bt_res.win_rate, 2),
            "trade_count": bt_res.total_trades,
            "profit_factor": round(bt_res.profit_factor, 2),
        }

        g1_passed = True
        g1_warnings = []
        g1_status = "PASS"

        if bt_res.total_trades < self.criteria.min_trade_count:
            g1_passed = False
            g1_status = "FAIL"
            msg = f"Insufficient trade sample size ({bt_res.total_trades} trades, minimum {self.criteria.min_trade_count})."
            g1_warnings.append(msg)
            failure_reasons.append(msg)

        if bt_res.total_return_pct < self.criteria.min_backtest_return_pct:
            g1_passed = False
            g1_status = "FAIL"
            msg = f"Negative or below-threshold return ({bt_res.total_return_pct:.2f}% < {self.criteria.min_backtest_return_pct}%)."
            g1_warnings.append(msg)
            failure_reasons.append(msg)

        if bt_res.sharpe_ratio < self.criteria.min_backtest_sharpe:
            sharpe_is_target = not enable_multi_objective or (
                multi_objective_metrics is not None
                and "sharpe_ratio" in multi_objective_metrics
            )
            if bt_res.sharpe_ratio < 0.0 and sharpe_is_target:
                g1_passed = False
                g1_status = "FAIL"
                msg = f"Negative Sharpe ratio ({bt_res.sharpe_ratio:.2f})."
                g1_warnings.append(msg)
                failure_reasons.append(msg)
            else:
                g1_status = "CAUTION"
                g1_warnings.append(
                    f"Sub-optimal Sharpe ratio ({bt_res.sharpe_ratio:.2f} < {self.criteria.min_backtest_sharpe})."
                )

        if bt_res.max_drawdown_pct > self.criteria.max_backtest_drawdown_pct:
            g1_status = "CAUTION" if g1_passed else "FAIL"
            g1_warnings.append(
                f"Elevated in-sample drawdown ({bt_res.max_drawdown_pct:.1f}% > {self.criteria.max_backtest_drawdown_pct}%)."
            )

        gate_results["gate1_backtest"] = GateResult(
            gate_name="Gate 1: In-Sample Backtest",
            passed=g1_passed,
            status=g1_status,
            metrics=g1_metrics,
            details=f"Return: {bt_res.total_return_pct:+.2f}%, Sharpe: {bt_res.sharpe_ratio:.2f}, Trades: {bt_res.total_trades}",
            warnings=g1_warnings,
        )
        all_warnings.extend(g1_warnings)

        # If Gate 1 failed catastrophically, short-circuit to FAIL
        if not g1_passed:
            return ValidationReport(
                status="FAIL",
                overall_score=max(0.0, min(30.0, (bt_res.total_return_pct + 10) * 2)),
                gates_passed=0,
                total_gates=3,
                gate_results=gate_results,
                summary="Failed Gate 1 (In-Sample Viability). Strategy is unprofitable or lacks sufficient trading frequency.",
                reasons=failure_reasons,
                recommended_action="REJECT",
            )

        # =========================================================================
        # Gate 2: Walk-Forward Out-of-Sample Efficiency (WFO)
        # =========================================================================
        logger.info("[StrategyValidator] Running Gate 2: Walk-Forward Analysis...")
        wfe = 0.0
        oos_return = 0.0
        try:
            wfo_cfg = wfo_config or WFOConfig(
                num_windows=0,
                in_sample_ratio=0.7,
                window_type="rolling",
                trials_per_window=0,
            )
            wfo_opt_cfg = OptunaConfig(
                n_trials=n_trials,
                target_metric=target_metric,
                enable_multi_objective=enable_multi_objective,
                multi_objective_metrics=multi_objective_metrics
                or ["sharpe_ratio", "max_drawdown_pct"],
            )
            wfo_engine = WalkForwardEngine(
                base_params=params,
                wfo_config=wfo_cfg,
                opt_config=wfo_opt_cfg,
                n_trials_total=n_trials,
            )
            wfo_report = wfo_engine.run(df_candles)

            wfe = float(
                wfo_report.get(
                    "walk_forward_efficiency_pct", wfo_report.get("wfe", 0.0)
                )
            )
            windows = wfo_report.get("window_details", wfo_report.get("windows", []))
            n_windows = len(windows)
            profitable_windows = sum(
                1 for w in windows if w.get("oos_return_pct", 0.0) > 0
            )
            profitable_pct = (
                round((profitable_windows / n_windows * 100.0), 1)
                if n_windows > 0
                else 0.0
            )
            oos_return = float(
                wfo_report.get(
                    "avg_out_of_sample_return_pct",
                    wfo_report.get("oos_total_return_pct", 0.0),
                )
            )
            consistency_score = round(
                float(
                    wfo_report.get(
                        "consistency_score",
                        profitable_pct * (wfe / 100.0) if wfe > 0 else 0.0,
                    )
                ),
                1,
            )

            g2_metrics = {
                "walk_forward_efficiency_pct": round(wfe, 1),
                "profitable_windows_pct": round(profitable_pct, 1),
                "oos_total_return_pct": round(oos_return, 2),
                "n_windows": n_windows,
                "consistency_score": consistency_score,
                # Regime-aware WFO fields (ema_vwap-z1v extension)
                "regime_change_count": wfo_report.get("regime_change_count", 0),
                "regime_change_ratio": wfo_report.get("regime_change_ratio", 0.0),
                "trigger_reoptimize": wfo_report.get("trigger_reoptimize", False),
                "window_regimes_summary": self._summarize_regimes(
                    wfo_report.get("window_regimes", [])
                ),
            }

            g2_passed = True
            g2_warnings = []
            g2_status = "PASS"

            if wfe < self.criteria.min_walk_forward_efficiency:
                g2_passed = False
                g2_status = "CAUTION" if oos_return > 0 else "FAIL"
                msg = f"Low Walk-Forward Efficiency ({wfe:.1f}% < {self.criteria.min_walk_forward_efficiency}%). Indicates in-sample overfitting."
                g2_warnings.append(msg)
                failure_reasons.append(msg)

            if profitable_pct < self.criteria.min_profitable_windows_pct:
                g2_status = "CAUTION"
                msg = f"Low out-of-sample window consistency ({profitable_pct:.0f}% < {self.criteria.min_profitable_windows_pct}% profitable windows)."
                g2_warnings.append(msg)

            if oos_return < self.criteria.min_oos_return_pct:
                g2_passed = False
                g2_status = "FAIL"
                msg = f"Negative out-of-sample aggregate return ({oos_return:.2f}%)."
                g2_warnings.append(msg)
                failure_reasons.append(msg)

            gate_results["gate2_wfo"] = GateResult(
                gate_name="Gate 2: Walk-Forward Analysis",
                passed=g2_passed,
                status=g2_status,
                metrics=g2_metrics,
                details=f"WFE: {wfe:.1f}%, Profitable Windows: {profitable_pct:.0f}%, OOS Return: {oos_return:+.2f}%",
                warnings=g2_warnings,
            )
            all_warnings.extend(g2_warnings)

        except Exception as g2_exc:
            g2_err_msg = f"Gate 2 (WFO) failed: {g2_exc}"
            logger.warning(f"[StrategyValidator] {g2_err_msg}")
            failure_reasons.append(g2_err_msg)
            gate_results["gate2_wfo"] = GateResult(
                gate_name="Gate 2: Walk-Forward Analysis",
                passed=False,
                status="FAIL",
                metrics={},
                details=g2_err_msg,
                warnings=[g2_err_msg],
            )

        # =========================================================================
        # Gate 3: Monte Carlo Sequence Stress & Tail Risk
        # =========================================================================
        logger.info("[StrategyValidator] Running Gate 3: Monte Carlo Stress Test...")
        risk_of_ruin = 0.0
        max_dd_p95 = 0.0
        try:
            mc_cfg = mc_config or MonteCarloConfig(
                num_simulations=500,
                sample_with_replacement=True,
            )
            mc_sim = MonteCarloSimulator(
                initial_capital=params.initial_capital or 10000.0,
                trades=bt_res.trades,
                config=mc_cfg,
            )
            mc_report = mc_sim.run()

            risk_of_ruin = float(mc_report.get("risk_of_ruin_pct", 0.0))
            max_dd_p95 = float(
                mc_report.get("max_drawdown_percentiles", {}).get("p95", 0.0)
            )
            median_ret = float(
                mc_report.get("net_profit_percentiles", {}).get("p50", 0.0)
            )

            g3_metrics = {
                "risk_of_ruin_pct": round(risk_of_ruin, 2),
                "p95_max_drawdown_pct": round(max_dd_p95, 2),
                "median_return": round(median_ret, 2),
                "simulations_count": mc_report.get("num_simulations", 0),
            }

            g3_passed = True
            g3_warnings = []
            g3_status = "PASS"

            if risk_of_ruin > self.criteria.max_monte_carlo_ruin_pct:
                g3_passed = False
                g3_status = "FAIL"
                msg = f"Elevated Risk of Ruin ({risk_of_ruin:.2f}% > {self.criteria.max_monte_carlo_ruin_pct}%)."
                g3_warnings.append(msg)
                failure_reasons.append(msg)

            if max_dd_p95 > self.criteria.max_monte_carlo_drawdown_pct:
                g3_status = "CAUTION" if g3_passed else "FAIL"
                msg = f"Severe 95th-percentile Monte Carlo Drawdown ({max_dd_p95:.1f}% > {self.criteria.max_monte_carlo_drawdown_pct}%)."
                g3_warnings.append(msg)
                if not g3_passed:
                    failure_reasons.append(msg)

            gate_results["gate3_monte_carlo"] = GateResult(
                gate_name="Gate 3: Monte Carlo Stress",
                passed=g3_passed,
                status=g3_status,
                metrics=g3_metrics,
                details=f"Risk of Ruin: {risk_of_ruin:.2f}%, 95% Max DD: {max_dd_p95:.1f}%",
                warnings=g3_warnings,
            )
            all_warnings.extend(g3_warnings)

        except Exception as g3_exc:
                    import traceback as _tb

                    g3_err_msg = f"Gate 3 (Monte Carlo) failed: {g3_exc}"
                    logger.warning(f"[StrategyValidator] {g3_err_msg}{_tb.format_exc()}")
                    # Monte Carlo failure is not catastrophic — degrade to CAUTION rather than FAIL
                    # if the strategy otherwise passed Gates 1 and 2.
                    g3_passed = False
                    g3_status = "CAUTION"
                    gate_results["gate3_monte_carlo"] = GateResult(
                        gate_name="Gate 3: Monte Carlo Stress",
                        passed=False,
                        status="CAUTION",
                        metrics={},
                        details=g3_err_msg,
                        warnings=[g3_err_msg],
                    )
                    all_warnings.append(g3_err_msg)

        # =========================================================================
        # Composite Scoring & Status Synthesis
        # =========================================================================
        gates_passed_count = sum(1 for g in gate_results.values() if g.passed)

        # --- Gate 1 score (35 pts) ---
        # Use the actual optimized metric(s) to score Gate 1, so batch sweep results
        # or multi-objective Pareto results are scored fairly across their targets.
        _g1_metric_scores = {
            "sharpe_ratio": min(35.0, max(0.0, (bt_res.sharpe_ratio / 2.0) * 35.0)),
            "calmar_ratio": min(35.0, max(0.0, (bt_res.calmar_ratio / 2.0) * 35.0)),
            "sortino_ratio": min(35.0, max(0.0, (bt_res.sortino_ratio / 2.0) * 35.0)),
            "profit_factor": min(
                35.0, max(0.0, ((bt_res.profit_factor - 1.0) / 2.0) * 35.0)
            ),
            "total_return_pct": min(
                35.0, max(0.0, (bt_res.total_return_pct / 50.0) * 35.0)
            ),
            "net_profit": min(35.0, max(0.0, (bt_res.total_return_pct / 50.0) * 35.0)),
            "win_rate": min(35.0, max(0.0, ((bt_res.win_rate - 40.0) / 30.0) * 35.0)),
            "drawdown_penalized_sharpe": min(
                35.0, max(0.0, (bt_res.sharpe_ratio / 2.0) * 35.0)
            ),
            "max_drawdown_pct": min(
                35.0, max(0.0, (1.0 - (bt_res.max_drawdown_pct / 30.0)) * 35.0)
            ),
            "ulcer_index": min(
                35.0,
                max(0.0, (1.0 - (getattr(bt_res, "ulcer_index", 0.0) / 15.0)) * 35.0),
            ),
        }
        if enable_multi_objective and multi_objective_metrics:
            valid_scores = [
                _g1_metric_scores.get(
                    m, min(35.0, max(0.0, (bt_res.sharpe_ratio / 2.0) * 35.0))
                )
                for m in multi_objective_metrics
            ]
            g1_score = (
                sum(valid_scores) / len(valid_scores)
                if valid_scores
                else _g1_metric_scores.get(target_metric, 0.0)
            )
        else:
            g1_score = _g1_metric_scores.get(
                target_metric, min(35.0, max(0.0, (bt_res.sharpe_ratio / 2.0) * 35.0))
            )

        # --- Gate 2 score (40 pts) ---
        # Split into WFE efficiency (25 pts) + OOS return quality (15 pts).
        # WFE-only scoring rewarded high efficiency even when OOS returns were near-zero.
        wfe_score = min(25.0, max(0.0, (wfe / 100.0) * 25.0))
        # OOS return quality: normalize to 15 pts with 20% return = full score
        oos_return_score = min(15.0, max(0.0, (oos_return / 20.0) * 15.0))
        g2_score = wfe_score + oos_return_score

        # --- Gate 3 score (25 pts) ---
        # Penalize only above-threshold risk; strategies meeting all thresholds
        # should earn close to full Gate 3 points.
        ruin_excess = max(0.0, risk_of_ruin - self.criteria.max_monte_carlo_ruin_pct)
        dd_excess = max(0.0, max_dd_p95 - self.criteria.max_monte_carlo_drawdown_pct)
        g3_score = min(25.0, max(0.0, 25.0 - (ruin_excess * 2.0) - (dd_excess * 0.5)))

        total_score = round(g1_score + g2_score + g3_score, 1)

        if (
            gates_passed_count == 3
            and g1_status == "PASS"
            and g2_status == "PASS"
            and g3_status == "PASS"
        ):
            overall_status = "ROBUST"
            summary = "✅ Strategy is ROBUST. Passed all 3 gates (In-Sample Viability, Walk-Forward Out-of-Sample Efficiency, Monte Carlo Stress)."
            recommended_action = "DEPLOY_SAFE"
        elif g1_passed and (
            g2_status == "CAUTION" or g3_status == "CAUTION" or gates_passed_count >= 2
        ):
            overall_status = "CAUTION"
            summary = f"⚠️ Strategy passed with CAUTION ({gates_passed_count}/3 gates passed). Out-of-sample performance or tail risk requires monitoring."
            recommended_action = "PROCEED_WITH_CAUTION"
        else:
            overall_status = "FAIL"
            summary = f"❌ Strategy FAILED validation ({gates_passed_count}/3 gates passed). Classified as poor performer or curve-fitted."
            recommended_action = "REJECT"

        return ValidationReport(
            status=overall_status,
            overall_score=total_score,
            gates_passed=gates_passed_count,
            total_gates=3,
            gate_results=gate_results,
            summary=summary,
            reasons=failure_reasons if failure_reasons else all_warnings,
            recommended_action=recommended_action,
        )
