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
        trades: list[dict[str, Any]],
        config: MonteCarloConfig | None = None,
    ):
        self.initial_capital = initial_capital
        self.trades = trades
        self.config = config or MonteCarloConfig()

    def run(self) -> dict[str, Any]:
        """Run N Monte Carlo simulations over trade log."""
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

        # Uniform length for interpolated equity curves (100 points)
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

            # Check Risk of Ruin (50% drawdown or capital depletion)
            peak = np.maximum.accumulate(equity_path)
            peak_safe = np.where(peak <= 0, 1e-5, peak)
            drawdowns = (peak - equity_path) / peak_safe
            max_dd_pct = float(np.clip(np.max(drawdowns) * 100.0, 0.0, 100.0))
            sim_max_drawdowns.append(max_dd_pct)

            if max_dd_pct >= 50.0 or final_eq <= (self.initial_capital * 0.5):
                ruin_count += 1

            # Sharpe calculation for path with protection against zero or negative equity.
            # Annualized with sqrt(252) — standard trading days per year — matching the
            # backtest engine's convention so percentile Sharpe values are comparable.
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

            # Resample equity path to standard grid for fan chart
            grid_x = np.linspace(0, n_trades, num_points)
            orig_x = np.arange(n_trades + 1)
            sim_equity_matrices[sim_idx, :] = np.interp(grid_x, orig_x, equity_path)

        # Compute Percentiles across simulations
        p5_final = float(np.nanpercentile(sim_final_equities, 5))
        p50_final = float(np.nanpercentile(sim_final_equities, 50))
        p95_final = float(np.nanpercentile(sim_final_equities, 95))

        p5_dd = float(np.nanpercentile(sim_max_drawdowns, 5))
        p50_dd = float(np.nanpercentile(sim_max_drawdowns, 50))
        p95_dd = float(np.nanpercentile(sim_max_drawdowns, 95))

        p5_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 5))
        p50_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 50))
        p95_sharpe = float(np.nanpercentile(sim_sharpe_ratios, 95))

        # Percentile curves over time
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

        # Histogram distribution of Max Drawdowns
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
