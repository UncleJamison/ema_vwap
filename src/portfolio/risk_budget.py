"""
Cross-Asset Risk Budgeting and Correlation-Adjusted Position Sizing Engine.
"""

from typing import Any

import numpy as np
import pandas as pd

from src.portfolio.correlation import CrossAssetCorrelationEngine
from src.portfolio.models import UnifiedPortfolioSnapshot
from src.portfolio.risk_engine import PortfolioRiskEngine


class CrossAssetRiskBudgeter:
    """Calculates risk-budgeted and correlation-penalized position sizes for multi-asset trading."""

    @staticmethod
    def calculate_position_size(
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        snapshot: UnifiedPortfolioSnapshot,
        risk_per_trade_pct: float = 1.0,
        returns_df: pd.DataFrame | None = None,
        allow_fractional: bool = True,
    ) -> dict[str, Any]:
        """
        Calculates correlation-adjusted position size for a prospective trade.
        """
        if entry_price <= 0:
            return {
                "symbol": symbol,
                "units": 0.0,
                "position_value_usd": 0.0,
                "risk_amount_usd": 0.0,
                "correlation_penalty": 1.0,
                "circuit_breaker_scale": 1.0,
                "final_risk_pct": 0.0,
                "reason": "Invalid entry price",
            }

        total_nav = max(1000.0, snapshot.total_nav_usd)
        stop_dist = abs(entry_price - stop_loss_price)
        if stop_dist <= 0:
            stop_dist = entry_price * 0.02  # Fallback 2% stop distance

        # 1. Base dollar risk
        base_risk_usd = total_nav * (risk_per_trade_pct / 100.0)

        # 2. Risk Engine Circuit Breaker Multiplier
        risk_metrics = PortfolioRiskEngine.compute_portfolio_var(
            snapshot, returns_df=returns_df
        )
        circuit_scale = risk_metrics.get("risk_scale_factor", 1.0)
        circuit_status = risk_metrics.get("circuit_breaker_status", "NORMAL")

        if circuit_scale <= 0:
            return {
                "symbol": symbol,
                "units": 0.0,
                "position_value_usd": 0.0,
                "risk_amount_usd": 0.0,
                "correlation_penalty": 0.0,
                "circuit_breaker_scale": 0.0,
                "final_risk_pct": 0.0,
                "circuit_breaker_status": circuit_status,
                "reason": "Drawdown circuit breaker active (EMERGENCY_HALT). Trading paused.",
            }

        # 3. Correlation Penalty against Open Positions
        correlation_penalty = 1.0
        avg_corr_with_open = 0.0

        if snapshot.positions:
            open_symbols = [p.symbol for p in snapshot.positions]
            if returns_df is None or returns_df.empty:
                all_syms = list(set(open_symbols + [symbol]))
                returns_df = CrossAssetCorrelationEngine.generate_synthetic_returns(
                    all_syms
                )

            if symbol in returns_df.columns:
                corr_vals = []
                for o_sym in open_symbols:
                    if o_sym in returns_df.columns:
                        r = float(returns_df[symbol].corr(returns_df[o_sym]))
                        corr_vals.append(r)
                if corr_vals:
                    avg_corr_with_open = float(np.mean(corr_vals))
                    if avg_corr_with_open > 0.40:
                        # Scale down proportionally as correlation exceeds 0.40
                        penalty = 1.0 - (avg_corr_with_open - 0.40) * 1.5
                        correlation_penalty = max(0.20, min(1.0, penalty))

        # 4. Final Sizing Calculation
        effective_risk_usd = base_risk_usd * circuit_scale * correlation_penalty
        raw_units = effective_risk_usd / stop_dist
        final_units = raw_units if allow_fractional else float(int(raw_units))
        position_val_usd = final_units * entry_price

        # Cap single position market value at 30% of total portfolio NAV
        max_position_cap = total_nav * 0.30
        if position_val_usd > max_position_cap:
            position_val_usd = max_position_cap
            raw_units = position_val_usd / entry_price
            final_units = raw_units if allow_fractional else float(int(raw_units))
            effective_risk_usd = final_units * stop_dist

        effective_risk_pct = (effective_risk_usd / total_nav) * 100.0

        return {
            "symbol": symbol,
            "entry_price": round(entry_price, 4),
            "stop_loss_price": round(stop_loss_price, 4),
            "stop_distance": round(stop_dist, 4),
            "units": round(final_units, 6) if allow_fractional else int(final_units),
            "position_value_usd": round(position_val_usd, 2),
            "effective_risk_usd": round(effective_risk_usd, 2),
            "effective_risk_pct": round(effective_risk_pct, 2),
            "base_risk_pct": risk_per_trade_pct,
            "correlation_penalty": round(correlation_penalty, 3),
            "avg_correlation_with_portfolio": round(avg_corr_with_open, 3),
            "circuit_breaker_scale": round(circuit_scale, 2),
            "circuit_breaker_status": circuit_status,
        }
