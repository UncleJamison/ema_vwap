"""
Factor Research Module (ema_vwap-pdt).

Provides:
- Pre-built alpha factors (momentum, mean reversion, volume, volatility)
- Information Coefficient (IC) analysis and decay curves
- Factor combination optimizer (mean-variance, risk-parity, HRP)
- Factor-neutral portfolio weight generation
"""

from src.options.greeks import (
    implied_volatility,
)

from .factors import (
    AlphaFactor,
    FactorMetadata,
    compute_all_factors,
    get_factor_categories,
    get_factor_metadata,
    list_factors,
    mean_reversion_factor,
    momentum_factor,
    risk_adjusted_momentum,
    volatility_factor,
    volume_factor,
)
from .ic_analysis import (
    ICResult,
    compute_ic,
    compute_ic_decay,
    ic_summary,
)
from .optimizer import (
    FactorOptimizerConfig,
    combine_factors,
    factor_neutral_weights,
    optimize_factor_portfolio,
)

__all__ = [
    "AlphaFactor",
    "FactorMetadata",
    "FactorOptimizerConfig",
    "ICResult",
    "combine_factors",
    "compute_all_factors",
    "compute_ic",
    "compute_ic_decay",
    "factor_neutral_weights",
    "get_factor_categories",
    "get_factor_metadata",
    "ic_summary",
    "implied_volatility",
    "list_factors",
    "mean_reversion_factor",
    "momentum_factor",
    "optimize_factor_portfolio",
    "portfolio_greeks",
    "risk_adjusted_momentum",
    "volatility_factor",
    "volume_factor",
]
