"""
Monte Carlo Simulation & Risk Analytics Engine.
Performs trade bootstrap resampling, sequence permutation, equity curve percentile fan calculation, and risk of ruin evaluation.
"""

from typing import Any

import numpy as np

from src.config import MonteCarloConfig


class MonteCarloSimulator:
    """Simulates trade series permutations and bootstrap resampling to evaluate risk bounds."""

    def __init__(
        self,
        initial_capital: float,
        trades: list[dict[str, Any]] | None = None,
        config: MonteCarloConfig | None = None,
        return_series: list[float] | None = None,
        equity_curve: list[float] | None = None,
    ):
        self.initial_capital = initial_capital
        self.trades = trades or []
        self.return_series = return_series or []
        self.equity_curve = equity_curve or []
        self.config = config or MonteCarloConfig()

    def _normalize_return_series(self) -> list[float]:
        """Normalize relative-return inputs from OOS windows to a per-step return series."""
        if self.return_series:
            normalized = []
            for value in self.return_series:
                val = float(value)
                if abs(val) > 1.0:
                    val = val / 100.0
                normalized.append(val)
            return normalized

        if self.equity_curve:
            curve = np.asarray(self.equity_curve, dtype=float)
            if curve.size < 2:
                return []
            if np.all(np.abs(curve) <= 1.5):
                return [float(v) for v in curve]
            prev = np.concatenate(([curve[0]], curve[:-1]))
            denom = np.where(np.abs(prev) < 1e-9, 1.0, np.abs(prev))
            rel = (curve - prev) / denom
            return [float(v) for v in rel[1:]]

        return []

    def _run_relative_return_path(self) -> dict[str, Any]:
        """Stress-test based on OOS relative-return series instead of fixed-dollar trade PnL."""
        return_series = self._normalize_return_series()
        if not return_series:
            return self._run_trade_based_simulation()

        n_steps = len(return_series)
        n_sims = self.config.num_simulations
        if n_sims < 1:
            raise ValueError("num_simulations must be at least 1")

        if self.config.random_seed is not None:
            np.random.seed(self.config.random_seed)

        sim_final_equities = []
        sim_max_drawdowns = []
        sim_sharpe_ratios = []
        ruin_count = 0
        num_points = 100
        sim_equity_matrices = np.zeros((n_sims, num_points))

        for sim_idx in range(n_sims):
            if self.config.sample_with_replacement:
                sampled_returns = np.random.choice(
                    return_series, size=n_steps, replace=True
                )
            else:
                sampled_returns = np.random.permutation(return_series)

            equity_path = np.empty(n_steps + 1, dtype=float)
            equity_path[0] = self.initial_capital
            for i, ret in enumerate(sampled_returns):
                equity_path[i + 1] = equity_path[i] * (1.0 + float(ret))

            final_eq = float(equity_path[-1])
            sim_final_equities.append(final_eq)

            peak = np.maximum.accumulate(equity_path)
            peak_safe = np.where(peak <= 0, 1e-5, peak)
            drawdowns = (peak - equity_path) / peak_safe
            max_dd_pct = float(np.clip(np.max(drawdowns) * 100.0, 0.0, 100.0))
            sim_max_drawdowns.append(max_dd_pct)

            if max_dd_pct >= 50.0 or final_eq <= (self.initial_capital * 0.5):
                ruin_count += 1

            rets = np.diff(equity_path) / np.where(
                equity_path[:-1] <= 0, np.nan, equity_path[:-1]
            )
            valid_rets = rets[np.isfinite(rets)]
            if len(valid_rets) > 1:
                std_rets = float(np.std(valid_rets))
                sharpe = (
                    float((np.mean(valid_rets) / std_rets) * np.sqrt(252))
                    if std_rets > 0
                    else 0.0
                )
            else:
                sharpe = 0.0
            sim_sharpe_ratios.append(sharpe)

            grid_x = np.linspace(0, n_steps, num_points)
            orig_x = np.arange(n_steps + 1)
            sim_equity_matrices[sim_idx, :] = np.interp(grid_x, orig_x, equity_path)

        p5_final = float(np.nanpercentile(sim_final_equities, 5))
        p50_final = float(np.nanpercentile(sim_final_equities, 50))
        p95_final = float(np.nanpercentile(sim_final_equities, 95))
        p5_dd = float(np.nanpercentile(sim_max_drawdowns, 5))
        p50_dd = float(np.nanpercentile(sim_max_drawdowns, 50))
        p95_dd = float(np.nanpercentile(sim_max_drawdowns, 95))
        p5_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 5))
        p50_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 50))
        p95_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 95))

        curve_p5 = np.nanpercentile(sim_equity_matrices, 5, axis=0)
        curve_p50 = np.nanpercentile(sim_equity_matrices, 50, axis=0)
        curve_p95 = np.nanpercentile(sim_equity_matrices, 95, axis=0)

        step_pct = 100.0 / (num_points - 1)
        equity_curves_formatted = {
            "p5": [
                {"step": round(i * step_pct, 1), "equity": round(float(val), 2)}
                for i, val in enumerate(curve_p5)
            ],
            "p50": [
                {"step": round(i * step_pct, 1), "equity": round(float(val), 2)}
                for i, val in enumerate(curve_p50)
            ],
            "p95": [
                {"step": round(i * step_pct, 1), "equity": round(float(val), 2)}
                for i, val in enumerate(curve_p95)
            ],
        }

        counts, bin_edges = np.histogram(sim_max_drawdowns, bins=15)
        dd_histogram = [
            {
                "bin_start": round(float(bin_edges[i]), 1),
                "bin_end": round(float(bin_edges[i + 1]), 1),
                "count": int(counts[i]),
            }
            for i in range(len(counts))
        ]

        return {
            "num_simulations": n_sims,
            "trade_count": n_steps,
            "sample_with_replacement": self.config.sample_with_replacement,
            "risk_of_ruin_pct": round((ruin_count / n_sims) * 100.0, 2),
            "final_equity_percentiles": {
                "p5": round(p5_final, 2),
                "p50": round(p50_final, 2),
                "p95": round(p95_final, 2),
            },
            "net_profit_percentiles": {
                "p5": round(p5_final - self.initial_capital, 2),
                "p50": round(p50_final - self.initial_capital, 2),
                "p95": round(p95_final - self.initial_capital, 2),
            },
            "max_drawdown_percentiles": {
                "p5": round(p5_dd, 2),
                "p50": round(p50_dd, 2),
                "p95": round(p95_dd, 2),
            },
            "sharpe_ratio_percentiles": {
                "p5": round(p5_sharpe, 2),
                "p50": round(p50_sharpe, 2),
                "p95": round(p95_sharpe, 2),
            },
            "equity_curves_percentiles": equity_curves_formatted,
            "drawdown_histogram": dd_histogram,
        }

    def _run_trade_based_simulation(self) -> dict[str, Any]:
        """Legacy Monte Carlo path that operates on fixed-dollar trade PnLs."""
        if not self.trades or len(self.trades) < 2:
            return {
                "num_simulations": 0,
                "trade_count": len(self.trades) if self.trades else 0,
                "message": "Insufficient trades for Monte Carlo simulation (minimum 2 trades required).",
                "risk_of_ruin_pct": 0.0,
                "final_equity_percentiles": {
                    "p5": self.initial_capital,
                    "p50": self.initial_capital,
                    "p95": self.initial_capital,
                },
                "net_profit_percentiles": {"p5": 0.0, "p50": 0.0, "p95": 0.0},
                "max_drawdown_percentiles": {"p5": 0.0, "p50": 0.0, "p95": 0.0},
                "sharpe_ratio_percentiles": {"p5": 0.0, "p50": 0.0, "p95": 0.0},
                "equity_curves_percentiles": {"p5": [], "p50": [], "p95": []},
                "drawdown_histogram": [],
            }

        pnls = [t.get("pnl", 0.0) for t in self.trades]
        n_trades = len(pnls)
        n_sims = self.config.num_simulations
        if n_sims < 1:
            raise ValueError("num_simulations must be at least 1")

        if self.config.random_seed is not None:
            np.random.seed(self.config.random_seed)

        sim_final_equities = []
        sim_max_drawdowns = []
        sim_sharpe_ratios = []
        ruin_count = 0

        num_points = 100
        sim_equity_matrices = np.zeros((n_sims, num_points))

        for sim_idx in range(n_sims):
            if self.config.sample_with_replacement:
                sampled_pnls = np.random.choice(pnls, size=n_trades, replace=True)
            else:
                sampled_pnls = np.random.permutation(pnls)

            equity_path = np.zeros(n_trades + 1)
            equity_path[0] = self.initial_capital

            for t_idx in range(n_trades):
                equity_path[t_idx + 1] = equity_path[t_idx] + sampled_pnls[t_idx]

            final_eq = equity_path[-1]
            sim_final_equities.append(final_eq)

            peak = np.maximum.accumulate(equity_path)
            peak_safe = np.where(peak <= 0, 1e-5, peak)
            drawdowns = (peak - equity_path) / peak_safe
            max_dd_pct = float(np.clip(np.max(drawdowns) * 100.0, 0.0, 100.0))
            sim_max_drawdowns.append(max_dd_pct)

            if max_dd_pct >= 50.0 or final_eq <= (self.initial_capital * 0.5):
                ruin_count += 1

            equity_prev = equity_path[:-1]
            equity_prev_safe = np.where(equity_prev <= 0, np.nan, equity_prev)
            rets = np.diff(equity_path) / equity_prev_safe
            valid_rets = rets[np.isfinite(rets)]
            if len(valid_rets) > 1:
                std_rets = float(np.std(valid_rets))
                if std_rets > 0:
                    sharpe = float((np.mean(valid_rets) / std_rets) * np.sqrt(252))
                else:
                    sharpe = 0.0
            else:
                sharpe = 0.0
            sim_sharpe_ratios.append(sharpe)

            grid_x = np.linspace(0, n_trades, num_points)
            orig_x = np.arange(n_trades + 1)
            sim_equity_matrices[sim_idx, :] = np.interp(grid_x, orig_x, equity_path)

        p5_final = float(np.nanpercentile(sim_final_equities, 5))
        p50_final = float(np.nanpercentile(sim_final_equities, 50))
        p95_final = float(np.nanpercentile(sim_final_equities, 95))

        p5_dd = float(np.nanpercentile(sim_max_drawdowns, 5))
        p50_dd = float(np.nanpercentile(sim_max_drawdowns, 50))
        p95_dd = float(np.nanpercentile(sim_max_drawdowns, 95))

        p5_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 5))
        p50_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 50))
        p95_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 95))

        curve_p5 = np.nanpercentile(sim_equity_matrices, 5, axis=0)
        curve_p50 = np.nanpercentile(sim_equity_matrices, 50, axis=0)
        curve_p95 = np.nanpercentile(sim_equity_matrices, 95, axis=0)

        step_pct = 100.0 / (num_points - 1)
        equity_curves_formatted = {
            "p5": [
                {"step": round(i * step_pct, 1), "equity": round(float(val), 2)}
                for i, val in enumerate(curve_p5)
            ],
            "p50": [
                {"step": round(i * step_pct, 1), "equity": round(float(val), 2)}
                for i, val in enumerate(curve_p50)
            ],
            "p95": [
                {"step": round(i * step_pct, 1), "equity": round(float(val), 2)}
                for i, val in enumerate(curve_p95)
            ],
        }

        counts, bin_edges = np.histogram(sim_max_drawdowns, bins=15)
        dd_histogram = [
            {
                "bin_start": round(float(bin_edges[i]), 1),
                "bin_end": round(float(bin_edges[i + 1]), 1),
                "count": int(counts[i]),
            }
            for i in range(len(counts))
        ]

        return {
            "num_simulations": n_sims,
            "trade_count": n_trades,
            "sample_with_replacement": self.config.sample_with_replacement,
            "risk_of_ruin_pct": round((ruin_count / n_sims) * 100.0, 2),
            "final_equity_percentiles": {
                "p5": round(p5_final, 2),
                "p50": round(p50_final, 2),
                "p95": round(p95_final, 2),
            },
            "net_profit_percentiles": {
                "p5": round(p5_final - self.initial_capital, 2),
                "p50": round(p50_final - self.initial_capital, 2),
                "p95": round(p95_final - self.initial_capital, 2),
            },
            "max_drawdown_percentiles": {
                "p5": round(p5_dd, 2),
                "p50": round(p50_dd, 2),
                "p95": round(p95_dd, 2),
            },
            "sharpe_ratio_percentiles": {
                "p5": round(p5_sharpe, 2),
                "p50": round(p50_sharpe, 2),
                "p95": round(p95_sharpe, 2),
            },
            "equity_curves_percentiles": equity_curves_formatted,
            "drawdown_histogram": dd_histogram,
        }

    def run(self) -> dict[str, Any]:
        """Run N Monte Carlo simulations over the configured evidence source."""
        if self.return_series or self.equity_curve:
            return self._run_relative_return_path()
        return self._run_trade_based_simulation()
