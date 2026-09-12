"""
Portfolio-Level Risk Budgeting Engine.

Provides:
- Risk parity weight construction (volatility-inverse weights from covariance)
- Per-asset max drawdown constraints (caps individual asset allocation by DD budget)
- Correlation-aware position sizing (already in CrossAssetRiskBudgeter)
- Portfolio-level Monte Carlo stress testing (simulates correlated asset paths)

This module extends the existing per-asset CrossAssetRiskBudgeter with
portfolio-construction logic that operates on the full covariance matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.portfolio.correlation import CrossAssetCorrelationEngine


@dataclass
class RiskBudgetConfig:
    """Configuration for portfolio-level risk budgeting."""

    # Risk parity target: each asset contributes equally to portfolio variance
    risk_parity: bool = True
    # Per-asset max drawdown budget (0.0 = no per-asset constraint)
    max_drawdown_per_asset_pct: float = 0.0
    # Max single-asset weight cap (e.g. 0.30 = no asset > 30% of portfolio)
    max_single_asset_weight: float = 0.30
    # Min single-asset weight floor (e.g. 0.02 = at least 2% if included)
    min_single_asset_weight: float = 0.0
    # Portfolio-level Monte Carlo simulations
    mc_simulations: int = 500
    # Confidence levels for percentile reporting
    confidence_levels: list[float] = field(default_factory=lambda: [5.0, 50.0, 95.0])
    # Risk-free rate for Sharpe calculation in MC
    risk_free_rate: float = 0.0
    # Annualization factor (trading days)
    annualization_factor: float = 252.0


@dataclass
class RiskBudgetResult:
    """Result of portfolio risk budgeting computation."""

    weights: dict[str, float]
    method: str
    risk_contributions: dict[str, float]
    portfolio_volatility: float
    diversification_ratio: float
    max_drawdown_constraints: dict[str, dict[str, float]]
    monte_carlo: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": {k: round(v, 6) for k, v in self.weights.items()},
            "method": self.method,
            "risk_contributions": {
                k: round(v, 6) for k, v in self.risk_contributions.items()
            },
            "portfolio_volatility": round(self.portfolio_volatility, 6),
            "diversification_ratio": round(self.diversification_ratio, 4),
            "max_drawdown_constraints": {
                k: {kk: round(vv, 6) for kk, vv in v.items()}
                for k, v in self.max_drawdown_constraints.items()
            },
            "monte_carlo": self.monte_carlo,
        }


class PortfolioRiskBudgetEngine:
    """Portfolio-level risk budgeting with risk parity and DD constraints."""

    @staticmethod
    def compute_risk_parity_weights(
        cov_matrix: np.ndarray,
        symbols: list[str],
        max_single_weight: float = 0.30,
        min_single_weight: float = 0.0,
    ) -> dict[str, float]:
        """
        Compute risk parity weights so each asset contributes equally
        to portfolio variance.

        Uses iterative proportional fitting (IPF) to solve:
            w_i * (Sigma @ w)_i = constant for all i

        Falls back to inverse-volatility weighting if convergence fails.
        """
        n = len(symbols)
        if n == 0:
            return {}
        if n == 1:
            return {symbols[0]: 1.0}

        # Ensure cov_matrix is valid
        cov = np.array(cov_matrix, dtype=float)
        if cov.shape != (n, n):
            # Fallback to diagonal from variances
            variances = np.diag(cov) if cov.ndim == 1 else np.ones(n)
            inv_vol = 1.0 / np.sqrt(np.maximum(variances, 1e-10))
            weights = inv_vol / inv_vol.sum()
            return {s: float(w) for s, w in zip(symbols, weights)}

        # Start from inverse-volatility initial weights
        vols = np.sqrt(np.maximum(np.diag(cov), 1e-10))
        inv_vol = 1.0 / vols
        weights = inv_vol / inv_vol.sum()

        # Iterative proportional fitting for risk parity
        for _ in range(100):
            port_var = float(weights @ cov @ weights)
            if port_var <= 0:
                break
            marginal_contribs = cov @ weights
            risk_contribs = weights * marginal_contribs
            target = port_var / n
            if target <= 0:
                break
            new_weights = weights * (target / np.maximum(risk_contribs, 1e-12))
            new_weights = new_weights / new_weights.sum()
            if np.max(np.abs(new_weights - weights)) < 1e-8:
                weights = new_weights
                break
            weights = new_weights

        # Apply max/min single-asset constraints
        weights = np.clip(weights, min_single_weight, max_single_weight)
        total = weights.sum()
        if total > 0:
            weights = weights / total

        return {s: float(w) for s, w in zip(symbols, weights)}

    @staticmethod
    def compute_inverse_volatility_weights(
        volatilities: list[float],
        symbols: list[str],
        max_single_weight: float = 0.30,
    ) -> dict[str, float]:
        """Compute inverse-volatility weights (simpler than risk parity)."""
        n = len(symbols)
        if n == 0:
            return {}
        vols = np.maximum(np.array(volatilities, dtype=float), 1e-10)
        inv_vol = 1.0 / vols
        weights = inv_vol / inv_vol.sum()
        weights = np.clip(weights, 0.0, max_single_weight)
        total = weights.sum()
        if total > 0:
            weights = weights / total
        return {s: float(w) for s, w in zip(symbols, weights)}

    @staticmethod
    def apply_drawdown_constraints(
        weights: dict[str, float],
        asset_drawdowns: dict[str, float],
        max_dd_per_asset_pct: float,
    ) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        """
        Apply per-asset max drawdown constraints.

        If an asset's historical drawdown exceeds max_dd_per_asset_pct,
        its weight is scaled down proportionally.

        Returns (adjusted_weights, constraint_details).
        """
        if max_dd_per_asset_pct <= 0:
            dd_details = {
                s: {
                    "historical_dd_pct": round(dd, 2),
                    "budget_pct": 0.0,
                    "scale_factor": 1.0,
                    "constrained": False,
                }
                for s, dd in asset_drawdowns.items()
            }
            return weights, dd_details

        adjusted = {}
        details = {}
        for sym, w in weights.items():
            asset_dd = asset_drawdowns.get(sym, 0.0)
            if asset_dd > max_dd_per_asset_pct:
                # Scale weight down proportionally
                scale = max_dd_per_asset_pct / max(asset_dd, 1e-6)
                scale = min(1.0, max(0.0, scale))
                adjusted[sym] = w * scale
                details[sym] = {
                    "historical_dd_pct": round(asset_dd, 2),
                    "budget_pct": max_dd_per_asset_pct,
                    "scale_factor": round(scale, 4),
                    "constrained": True,
                }
            else:
                adjusted[sym] = w
                details[sym] = {
                    "historical_dd_pct": round(asset_dd, 2),
                    "budget_pct": max_dd_per_asset_pct,
                    "scale_factor": 1.0,
                    "constrained": False,
                }

        # Re-normalize
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {s: w / total for s, w in adjusted.items()}

        return adjusted, details

    @staticmethod
    def compute_portfolio_monte_carlo(
        weights: dict[str, float],
        returns_df: pd.DataFrame,
        initial_capital: float = 10000.0,
        num_simulations: int = 500,
        horizon_days: int = 252,
        confidence_levels: list[float] | None = None,
        risk_free_rate: float = 0.0,
        annualization_factor: float = 252.0,
    ) -> dict[str, Any]:
        """
        Run portfolio-level Monte Carlo simulation using correlated
        multivariate normal returns from the covariance matrix.

        Simulates correlated asset paths and computes portfolio-level
        equity curves, drawdown percentiles, and risk-of-ruin.
        """
        if confidence_levels is None:
            confidence_levels = [5.0, 50.0, 95.0]

        symbols = list(weights.keys())
        n_assets = len(symbols)

        if n_assets == 0 or returns_df.empty:
            return {
                "num_simulations": 0,
                "message": "Insufficient data for portfolio MC simulation",
                "final_equity_percentiles": {},
                "max_drawdown_percentiles": {},
                "risk_of_ruin_pct": 0.0,
                "portfolio_sharpe": 0.0,
            }

        # Build covariance matrix and mean returns
        available = [s for s in symbols if s in returns_df.columns]
        if len(available) < n_assets:
            # Fill missing columns with zero-mean random returns
            for s in symbols:
                if s not in returns_df.columns:
                    returns_df = returns_df.copy()
                    returns_df[s] = np.random.normal(0, 0.015, len(returns_df))

        aligned = returns_df[symbols].fillna(0.0)
        cov_matrix = aligned.cov().values
        mean_returns = aligned.mean().values

        weight_arr = np.array([weights[s] for s in symbols])
        # Normalize weights
        weight_arr = weight_arr / max(weight_arr.sum(), 1e-10)

        # Portfolio daily mean and variance
        port_daily_mean = float(weight_arr @ mean_returns)
        port_daily_var = float(weight_arr @ cov_matrix @ weight_arr)
        port_daily_std = float(np.sqrt(max(port_daily_var, 1e-12)))

        # Simulate correlated returns via Cholesky decomposition
        try:
            cholesky = np.linalg.cholesky(cov_matrix + 1e-10 * np.eye(n_assets))
        except np.linalg.LinAlgError:
            # Fallback: use diagonal (uncorrelated) simulation
            cholesky = np.diag(np.sqrt(np.maximum(np.diag(cov_matrix), 1e-10)))

        rng = np.random.default_rng(seed=42)
        # Shape: (num_simulations, horizon_days)
        portfolio_returns = np.zeros((num_simulations, horizon_days))

        for sim in range(num_simulations):
            uncorrelated = rng.standard_normal((horizon_days, n_assets))
            correlated = uncorrelated @ cholesky.T + mean_returns
            daily_port_ret = correlated @ weight_arr
            portfolio_returns[sim, :] = daily_port_ret

        # Build equity curves
        equity_paths = np.zeros((num_simulations, horizon_days + 1))
        equity_paths[:, 0] = initial_capital
        for t in range(horizon_days):
            equity_paths[:, t + 1] = equity_paths[:, t] * (
                1.0 + portfolio_returns[:, t]
            )

        final_equities = equity_paths[:, -1]

        # Max drawdown per simulation
        max_drawdowns = np.zeros(num_simulations)
        for sim in range(num_simulations):
            eq = equity_paths[sim, :]
            peak = np.maximum.accumulate(eq)
            dd = (peak - eq) / np.maximum(peak, 1e-10)
            max_drawdowns[sim] = float(np.max(dd) * 100.0)

        # Risk of ruin: equity drops below 50% of initial
        ruin_count = np.sum(final_equities < initial_capital * 0.50)
        risk_of_ruin = float(ruin_count / num_simulations * 100.0)

        # Annualized portfolio Sharpe
        if port_daily_std > 0:
            annual_sharpe = float(
                (port_daily_mean - risk_free_rate / annualization_factor)
                / port_daily_std
                * np.sqrt(annualization_factor)
            )
        else:
            annual_sharpe = 0.0

        # Percentile calculations
        def pct(arr, p):
            return float(np.percentile(arr, p))

        final_eq_pct = {}
        max_dd_pct = {}
        for cl in confidence_levels:
            key = f"p{int(cl)}"
            final_eq_pct[key] = round(pct(final_equities, cl), 2)
            max_dd_pct[key] = round(pct(max_drawdowns, cl), 2)

        # Portfolio volatility (annualized)
        port_vol_annual = float(port_daily_std * np.sqrt(annualization_factor))

        return {
            "num_simulations": num_simulations,
            "horizon_days": horizon_days,
            "initial_capital": round(initial_capital, 2),
            "final_equity_percentiles": final_eq_pct,
            "max_drawdown_percentiles": max_dd_pct,
            "risk_of_ruin_pct": round(risk_of_ruin, 2),
            "portfolio_sharpe": round(annual_sharpe, 4),
            "portfolio_volatility_annual_pct": round(port_vol_annual * 100, 2),
            "portfolio_daily_mean_return_pct": round(port_daily_mean * 100, 4),
        }

    @staticmethod
    def compute_risk_budget(
        symbols: list[str],
        asset_metrics: dict[str, dict[str, float]] | None = None,
        returns_df: pd.DataFrame | None = None,
        config: RiskBudgetConfig | None = None,
    ) -> RiskBudgetResult:
        """
        Full portfolio risk budgeting computation.

        Args:
            symbols: List of asset symbols to budget across.
            asset_metrics: Dict of {symbol: {"max_drawdown_pct": float, "sharpe": float, ...}}
                           from batch sweep or backtest results.
            returns_df: DataFrame of asset returns for covariance estimation.
            config: RiskBudgetConfig with parameters.

        Returns:
            RiskBudgetResult with weights, risk contributions, DD constraints, and MC.
        """
        if config is None:
            config = RiskBudgetConfig()

        if not symbols:
            return RiskBudgetResult(
                weights={},
                method="risk_parity",
                risk_contributions={},
                portfolio_volatility=0.0,
                diversification_ratio=1.0,
                max_drawdown_constraints={},
                monte_carlo={},
            )

        # Generate or use provided returns
        if returns_df is None or returns_df.empty:
            returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(symbols)

        # Ensure all symbols have columns
        for s in symbols:
            if s not in returns_df.columns:
                returns_df = returns_df.copy()
                returns_df[s] = np.random.normal(0, 0.015, len(returns_df))

        aligned = returns_df[symbols].fillna(0.0)
        cov_matrix = aligned.cov().values

        # 1. Compute risk parity weights
        if config.risk_parity:
            weights = PortfolioRiskBudgetEngine.compute_risk_parity_weights(
                cov_matrix=cov_matrix,
                symbols=symbols,
                max_single_weight=config.max_single_asset_weight,
                min_single_weight=config.min_single_asset_weight,
            )
            method = "risk_parity"
        else:
            # Inverse volatility weighting
            vols = np.sqrt(np.maximum(np.diag(cov_matrix), 1e-10))
            weights = PortfolioRiskBudgetEngine.compute_inverse_volatility_weights(
                volatilities=list(vols),
                symbols=symbols,
                max_single_weight=config.max_single_asset_weight,
            )
            method = "inverse_volatility"

        # 2. Apply per-asset max drawdown constraints
        asset_dd = {}
        if asset_metrics:
            for s in symbols:
                m = asset_metrics.get(s, {})
                asset_dd[s] = m.get("max_drawdown_pct", 0.0)
        else:
            # Estimate from returns: use historical max drawdown of equal-weight portfolio
            for s in symbols:
                if s in aligned.columns:
                    cum = (1 + aligned[s]).cumprod()
                    peak = cum.cummax()
                    dd = ((peak - cum) / peak).max() * 100
                    asset_dd[s] = float(dd)
                else:
                    asset_dd[s] = 0.0

        weights, dd_constraints = PortfolioRiskBudgetEngine.apply_drawdown_constraints(
            weights=weights,
            asset_drawdowns=asset_dd,
            max_dd_per_asset_pct=config.max_drawdown_per_asset_pct,
        )

        # 3. Compute risk contributions
        weight_arr = np.array([weights.get(s, 0.0) for s in symbols])
        port_var = float(weight_arr @ cov_matrix @ weight_arr)
        port_vol = float(np.sqrt(max(port_var, 1e-12)))
        marginal_contribs = cov_matrix @ weight_arr
        risk_contribs = weight_arr * marginal_contribs
        risk_contrib_pct = (
            (risk_contribs / max(port_var, 1e-12) * 100)
            if port_var > 0
            else np.zeros(len(symbols))
        )

        risk_contributions = {s: float(rc) for s, rc in zip(symbols, risk_contrib_pct)}

        # 4. Diversification ratio
        asset_vols = np.sqrt(np.maximum(np.diag(cov_matrix), 1e-10))
        weighted_vol = float(weight_arr @ asset_vols)
        diversification_ratio = weighted_vol / port_vol if port_vol > 0 else 1.0

        # 5. Portfolio-level Monte Carlo
        mc_result = PortfolioRiskBudgetEngine.compute_portfolio_monte_carlo(
            weights=weights,
            returns_df=aligned,
            initial_capital=10000.0,
            num_simulations=config.mc_simulations,
            horizon_days=252,
            confidence_levels=config.confidence_levels,
            risk_free_rate=config.risk_free_rate,
            annualization_factor=config.annualization_factor,
        )

        return RiskBudgetResult(
            weights=weights,
            method=method,
            risk_contributions=risk_contributions,
            portfolio_volatility=port_vol,
            diversification_ratio=diversification_ratio,
            max_drawdown_constraints=dd_constraints,
            monte_carlo=mc_result,
        )
