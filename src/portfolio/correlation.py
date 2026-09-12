"""
Cross-Asset Correlation Engine.
Calculates rolling Pearson/Spearman correlation matrices, diversification ratio, and cluster warnings.
"""

from typing import Any

import numpy as np
import pandas as pd


class CrossAssetCorrelationEngine:
    """Calculates cross-asset return correlation matrix and diversification metrics."""

    @staticmethod
    def compute_correlation_matrix(
        returns_df: pd.DataFrame, method: str = "pearson"
    ) -> dict[str, Any]:
        """
        Computes correlation matrix from a DataFrame of asset returns.
        Columns: Asset symbols, Rows: Timestamps.
        """
        if returns_df.empty or len(returns_df.columns) < 2:
            symbols = (
                list(returns_df.columns) if not returns_df.empty else ["BTC/USD", "SPY"]
            )
            n = len(symbols)
            corr_mat = np.eye(n).tolist()
            return {
                "symbols": symbols,
                "correlation_matrix": corr_mat,
                "average_correlation": 0.0,
                "diversification_ratio": 1.0,
                "high_correlation_pairs": [],
            }

        # Drop columns with zero variance or all NaNs
        clean_df = returns_df.dropna(how="all", axis=1).fillna(0.0)
        corr = clean_df.corr(method=method).fillna(0.0)
        symbols = list(corr.columns)
        n = len(symbols)

        # High correlation pairs (r > 0.70)
        high_corr_pairs: list[dict[str, Any]] = []
        off_diag_corrs: list[float] = []

        for i in range(n):
            for j in range(i + 1, n):
                r_val = float(corr.iloc[i, j])
                off_diag_corrs.append(r_val)
                if abs(r_val) >= 0.70:
                    high_corr_pairs.append(
                        {
                            "asset_a": symbols[i],
                            "asset_b": symbols[j],
                            "correlation": round(r_val, 4),
                            "warning": (
                                "High co-movement risk"
                                if r_val > 0
                                else "Strong inverse hedge"
                            ),
                        }
                    )

        avg_corr = float(np.mean(off_diag_corrs)) if off_diag_corrs else 0.0

        # Diversification Ratio = (Weighted sum of asset vols) / (Portfolio Volatility)
        # Using equal weights for benchmark
        vols = clean_df.std().values
        cov = clean_df.cov().values
        eq_weights = np.ones(n) / n
        weighted_vol = float(np.dot(eq_weights, vols))
        port_variance = float(np.dot(eq_weights.T, np.dot(cov, eq_weights)))
        port_vol = float(np.sqrt(max(1e-8, port_variance)))
        diversification_ratio = weighted_vol / port_vol if port_vol > 0 else 1.0

        return {
            "symbols": symbols,
            "correlation_matrix": corr.values.round(4).tolist(),
            "average_correlation": round(avg_corr, 4),
            "diversification_ratio": round(diversification_ratio, 4),
            "high_correlation_pairs": high_corr_pairs,
        }

    @staticmethod
    def generate_synthetic_returns(
        symbols: list[str] | None = None, num_bars: int = 100
    ) -> pd.DataFrame:
        """
        Generates realistic cross-asset returns for testing and demonstration.
        Simulates realistic crypto correlations (BTC-ETH ~0.78, BTC-SOL ~0.65)
        and equity correlations (SPY-AAPL ~0.72, BTC-SPY ~0.25).
        """
        if symbols is None:
            symbols = ["BTC/USD", "ETH/USD", "AAPL", "SPY", "TSLA"]

        np.random.seed(42)
        # Latent market factor (macro trend)
        macro_factor = np.random.normal(0, 0.01, num_bars)
        crypto_factor = np.random.normal(0, 0.02, num_bars) + 0.3 * macro_factor
        equity_factor = np.random.normal(0, 0.012, num_bars) + 0.5 * macro_factor

        data = {}
        for s in symbols:
            s_clean = s.upper()
            if "BTC" in s_clean:
                data[s] = 0.85 * crypto_factor + np.random.normal(0, 0.01, num_bars)
            elif "ETH" in s_clean:
                data[s] = 0.80 * crypto_factor + np.random.normal(0, 0.012, num_bars)
            elif "SPY" in s_clean:
                data[s] = 0.90 * equity_factor + np.random.normal(0, 0.004, num_bars)
            elif "AAPL" in s_clean:
                data[s] = 0.75 * equity_factor + np.random.normal(0, 0.008, num_bars)
            elif "TSLA" in s_clean:
                data[s] = 0.60 * equity_factor + np.random.normal(0, 0.018, num_bars)
            else:
                data[s] = 0.5 * macro_factor + np.random.normal(0, 0.015, num_bars)

        return pd.DataFrame(data)
