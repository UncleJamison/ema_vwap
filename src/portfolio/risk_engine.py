"""
Portfolio Value at Risk (VaR), Conditional VaR (CVaR), and Risk Decomposition Engine.
"""

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

from src.portfolio.models import UnifiedPortfolioSnapshot


class PortfolioRiskEngine:
    """Computes portfolio-level Value at Risk, CVaR, Component VaR, and Drawdown Circuit Breakers."""

    @staticmethod
    def compute_portfolio_var(
        snapshot: UnifiedPortfolioSnapshot,
        returns_df: pd.DataFrame | None = None,
        confidence_level: float = 0.95,
        horizon_days: int = 1,
    ) -> dict[str, Any]:
        """
        Computes Parametric VaR, Historical VaR, CVaR, and Marginal Risk Contributions.
        """
        total_nav = snapshot.total_nav_usd
        if total_nav <= 0 or not snapshot.positions:
            return {
                "total_nav_usd": round(total_nav, 2),
                "param_var_usd": 0.0,
                "param_var_pct": 0.0,
                "hist_var_usd": 0.0,
                "hist_var_pct": 0.0,
                "cvar_usd": 0.0,
                "cvar_pct": 0.0,
                "confidence_level": confidence_level,
                "horizon_days": horizon_days,
                "component_var": {},
                "circuit_breaker_status": "NORMAL",
                "risk_scale_factor": 1.0,
            }

        positions = snapshot.positions
        symbols = [p.symbol for p in positions]
        weights = np.array([p.market_value_usd / total_nav for p in positions])

        if returns_df is None or returns_df.empty:
            from src.portfolio.correlation import CrossAssetCorrelationEngine

            returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(symbols)

        # Align columns
        for s in symbols:
            if s not in returns_df.columns:
                returns_df[s] = np.random.normal(0, 0.015, len(returns_df))

        aligned_returns = returns_df[symbols].fillna(0.0)
        cov_matrix = aligned_returns.cov().values

        # Portfolio Daily Volatility
        port_variance = float(np.dot(weights.T, np.dot(cov_matrix, weights)))
        port_std = float(np.sqrt(max(1e-8, port_variance))) * np.sqrt(horizon_days)

        # 1. Parametric VaR
        z_score = norm.ppf(confidence_level)
        param_var_pct = z_score * port_std * 100.0
        param_var_usd = total_nav * (z_score * port_std)

        # 2. Historical VaR & CVaR
        hist_port_returns = aligned_returns.dot(weights)
        alpha_quantile = 1.0 - confidence_level
        hist_var_return = -float(
            np.percentile(hist_port_returns, alpha_quantile * 100.0)
        )
        hist_var_pct = max(0.0, hist_var_return * 100.0 * np.sqrt(horizon_days))
        hist_var_usd = total_nav * (hist_var_pct / 100.0)

        # CVaR (Expected Shortfall) = Average loss beyond the VaR threshold
        tail_losses = hist_port_returns[hist_port_returns <= -hist_var_return]
        if len(tail_losses) > 0:
            cvar_pct = float(-tail_losses.mean() * 100.0 * np.sqrt(horizon_days))
        else:
            cvar_pct = hist_var_pct * 1.25
        cvar_usd = total_nav * (cvar_pct / 100.0)

        # 3. Component VaR (Marginal Contribution to Risk)
        # MCR = (Cov * w) / port_std
        marginal_contrib = (
            np.dot(cov_matrix, weights) / port_std if port_std > 0 else weights
        )
        component_vars = {}
        for i, sym in enumerate(symbols):
            comp_var_usd = weights[i] * marginal_contrib[i] * z_score * total_nav
            comp_var_pct = (
                (comp_var_usd / param_var_usd * 100.0) if param_var_usd > 0 else 0.0
            )
            component_vars[sym] = {
                "weight_pct": round(weights[i] * 100.0, 2),
                "market_value_usd": round(positions[i].market_value_usd, 2),
                "component_var_usd": round(comp_var_usd, 2),
                "risk_contribution_pct": round(comp_var_pct, 2),
            }

        # 4. Drawdown Circuit Breaker Calculation
        # Check unrealized PnL against total NAV
        unrealized_dd_pct = (
            abs(min(0.0, snapshot.total_unrealized_pnl_usd)) / total_nav * 100.0
            if total_nav > 0
            else 0.0
        )

        circuit_status = "NORMAL"
        risk_scale = 1.0

        if unrealized_dd_pct >= 15.0:
            circuit_status = "EMERGENCY_HALT"
            risk_scale = 0.0
        elif unrealized_dd_pct >= 10.0:
            circuit_status = "DE_RISKING"
            risk_scale = 0.25
        elif unrealized_dd_pct >= 5.0:
            circuit_status = "WARNING"
            risk_scale = 0.50

        return {
            "total_nav_usd": round(total_nav, 2),
            "param_var_usd": round(param_var_usd, 2),
            "param_var_pct": round(param_var_pct, 2),
            "hist_var_usd": round(hist_var_usd, 2),
            "hist_var_pct": round(hist_var_pct, 2),
            "cvar_usd": round(cvar_usd, 2),
            "cvar_pct": round(cvar_pct, 2),
            "confidence_level": confidence_level,
            "horizon_days": horizon_days,
            "unrealized_drawdown_pct": round(unrealized_dd_pct, 2),
            "circuit_breaker_status": circuit_status,
            "risk_scale_factor": risk_scale,
            "component_var": component_vars,
        }
