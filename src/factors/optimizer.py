"""
Factor Combination Optimizer (ema_vwap-pdt).

Provides:
- Factor combination methods (equal-weight, IC-weighted, mean-variance, HRP)
- Factor-neutral portfolio weight generation
- Factor timing signals
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.factors.ic_analysis import compute_multi_factor_ic


class CombinationMethod(Enum):
    EQUAL_WEIGHT = "equal_weight"
    IC_WEIGHTED = "ic_weighted"
    MEAN_VARIANCE = "mean_variance"
    RISK_PARITY = "risk_parity"
    HRP = "hrp"  # Hierarchical Risk Parity


@dataclass
class FactorOptimizerConfig:
    """Configuration for factor combination optimizer."""

    method: CombinationMethod = CombinationMethod.IC_WEIGHTED

    # Constraints
    max_weight: float = 0.3  # Max weight per factor
    min_weight: float = -0.1  # Min weight (allow slight short)
    leverage: float = 1.0  # Target gross leverage

    # Risk model
    risk_aversion: float = 1.0  # For mean-variance
    max_factor_corr: float = 0.9  # Max correlation between factors

    # IC weighting
    ic_horizon: int = 5  # Horizon for IC weighting
    ic_min_periods: int = 60  # Minimum periods for IC calc

    # Transaction costs
    turnover_penalty: float = 0.0  # bps per unit turnover

    # HRP
    hrp_linkage: str = "ward"  # linkage method for HRP


def equal_weight_combination(factor_names: list[str], **kwargs) -> np.ndarray:
    """Equal weight combination."""
    n = len(factor_names)
    return np.ones(n) / n


def ic_weighted_combination(
    factor_names: list[str], ic_values: dict[str, float], **kwargs
) -> np.ndarray:
    """Weight factors by their IC values."""
    ic_array = np.array([ic_values.get(name, 0.0) for name in factor_names])

    # Shift to positive and normalize
    ic_shifted = ic_array - ic_array.min() + 1e-6
    weights = ic_shifted / ic_shifted.sum()

    return weights


def mean_variance_combination(
    factor_returns: pd.DataFrame,
    factor_names: list[str],
    risk_aversion: float = 1.0,
    max_weight: float = 0.3,
    min_weight: float = -0.1,
    leverage: float = 1.0,
    **kwargs,
) -> np.ndarray:
    """
    Mean-variance optimization for factor weights.

    Maximizes: w' * mu - (risk_aversion/2) * w' * Sigma * w
    Subject to: sum(|w|) <= leverage, min_weight <= w_i <= max_weight
    """
    returns = factor_returns[factor_names].dropna()

    if len(returns) < 30:
        # Not enough data - fall back to equal weight
        return np.ones(len(factor_names)) / len(factor_names)

    mu = returns.mean().values * 252  # Annualized
    Sigma = returns.cov().values * 252  # Annualized

    n = len(factor_names)

    def objective(w):
        return -(w @ mu - 0.5 * risk_aversion * w @ Sigma @ w)

    # Constraints
    constraints = [
        {"type": "eq", "fun": lambda w: np.sum(np.abs(w)) - leverage},
    ]

    bounds = [(min_weight, max_weight)] * n

    # Initial guess: equal weight
    x0 = np.ones(n) / n

    result = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 1000},
    )

    if result.success:
        return result.x
    else:
        # Fallback
        return np.ones(n) / n


def risk_parity_combination(
    factor_returns: pd.DataFrame, factor_names: list[str], **kwargs
) -> np.ndarray:
    """
    Risk parity: equal risk contribution from each factor.

    Solves for weights where each factor contributes equally to portfolio volatility.
    """
    returns = factor_returns[factor_names].dropna()

    if len(returns) < 30:
        return np.ones(len(factor_names)) / len(factor_names)

    Sigma = returns.cov().values * 252
    n = len(factor_names)

    def risk_contribution(w):
        port_vol = np.sqrt(w @ Sigma @ w)
        mrc = (Sigma @ w) / port_vol  # Marginal risk contribution
        rc = w * mrc  # Risk contribution
        return rc

    def objective(w):
        rc = risk_contribution(w)
        target = np.sum(w @ Sigma @ w) / n  # Equal risk contribution
        return np.sum((rc - target) ** 2)

    bounds = [(0.01, 0.5)] * n
    x0 = np.ones(n) / n

    result = minimize(
        objective, x0, method="SLSQP", bounds=bounds, options={"maxiter": 1000}
    )

    if result.success:
        w = result.x
        return w / w.sum()
    else:
        return np.ones(n) / n


def hrp_combination(
    factor_returns: pd.DataFrame,
    factor_names: list[str],
    linkage: str = "ward",
    **kwargs,
) -> np.ndarray:
    """
    Hierarchical Risk Parity (HRP) for factor weights.

    Clusters factors by correlation, then allocates recursively.
    """
    try:
        from scipy.cluster.hierarchy import leaves_list
        from scipy.cluster.hierarchy import linkage as scipy_linkage
    except ImportError:
        return np.ones(len(factor_names)) / len(factor_names)

    returns = factor_returns[factor_names].dropna()

    if len(returns) < 30:
        return np.ones(len(factor_names)) / len(factor_names)

    corr = returns.corr()
    dist = np.sqrt((1 - corr) / 2)  # Distance metric

    # Hierarchical clustering (condensed distance vector required by scipy)
    from scipy.spatial.distance import squareform

    link = scipy_linkage(squareform(dist.values, checks=False), method=linkage)

    # Get cluster ordering
    order = leaves_list(link)

    # Recursive bisection allocation (operates on positional indices)
    def get_cluster_var(cov, items):
        w = np.ones(len(items)) / len(items)
        return float(w @ cov[np.ix_(items, items)] @ w)

    def get_hrp_weights(cov, items):
        if len(items) == 1:
            return {items[0]: 1.0}

        # Split cluster
        mid = len(items) // 2
        left_items = items[:mid]
        right_items = items[mid:]

        left_var = get_cluster_var(cov, left_items)
        right_var = get_cluster_var(cov, right_items)

        alpha = 1 - left_var / (left_var + right_var)

        left_w = get_hrp_weights(cov, left_items)
        right_w = get_hrp_weights(cov, right_items)

        # Combine
        weights = {}
        for k, v in left_w.items():
            weights[k] = alpha * v
        for k, v in right_w.items():
            weights[k] = (1 - alpha) * v

        return weights

    cov = returns.cov().values
    ordered_indices = [int(i) for i in order]
    w_dict = get_hrp_weights(cov, ordered_indices)
    return np.array([w_dict.get(i, 0.0) for i in range(len(factor_names))])


def combine_factors(
    factor_df: pd.DataFrame,
    config: FactorOptimizerConfig | None = None,
    ic_values: dict[str, float] | None = None,
    factor_returns: pd.DataFrame | None = None,
) -> np.ndarray:
    """
    Combine factors into composite signal using specified method.

    Args:
        factor_df: DataFrame of factor values (dates x factors)
        config: Optimizer configuration
        ic_values: Dict of factor -> IC value (for IC-weighted)
        factor_returns: Historical factor returns (for MV, RP, HRP)

    Returns:
        Array of factor weights (same order as factor_df.columns)
    """
    config = config or FactorOptimizerConfig()
    factor_names = factor_df.columns.tolist()

    if config.method == CombinationMethod.EQUAL_WEIGHT:
        return equal_weight_combination(factor_names)

    elif config.method == CombinationMethod.IC_WEIGHTED:
        if ic_values is None:
            # Compute IC from data
            ic_values = {}
            for name in factor_names:
                # Simplified: would need forward returns
                ic_values[name] = 0.0
        return ic_weighted_combination(factor_names, ic_values)

    elif config.method == CombinationMethod.MEAN_VARIANCE:
        if factor_returns is None:
            return equal_weight_combination(factor_names)
        return mean_variance_combination(
            factor_returns,
            factor_names,
            risk_aversion=config.risk_aversion,
            max_weight=config.max_weight,
            min_weight=config.min_weight,
            leverage=config.leverage,
        )

    elif config.method == CombinationMethod.RISK_PARITY:
        if factor_returns is None:
            return equal_weight_combination(factor_names)
        return risk_parity_combination(factor_returns, factor_names)

    elif config.method == CombinationMethod.HRP:
        if factor_returns is None:
            return equal_weight_combination(factor_names)
        return hrp_combination(factor_returns, factor_names, config.hrp_linkage)

    return equal_weight_combination(factor_names)


def factor_neutral_weights(
    factor_df: pd.DataFrame,
    target_exposures: dict[str, float] | None = None,
    factor_cov: pd.DataFrame | None = None,
    min_weight: float = -1.0,
    max_weight: float = 1.0,
) -> np.ndarray:
    """
    Compute factor-neutral portfolio weights.

    Finds portfolio weights that neutralize specified factor exposures
    while minimizing tracking error or maximizing alpha.

    Args:
        factor_df: Factor exposures (assets x factors)
        target_exposures: Target factor exposures (default: zero for all)
        factor_cov: Factor covariance matrix (optional)
        min_weight, max_weight: Weight bounds

    Returns:
        Portfolio weights (assets)
    """
    n_assets = len(factor_df)
    n_factors = factor_df.shape[1]

    if target_exposures is None:
        target = np.zeros(n_factors)
    else:
        target = np.array([target_exposures.get(f, 0.0) for f in factor_df.columns])

    B = factor_df.values  # n_assets x n_factors

    if factor_cov is None:
        factor_cov = np.eye(n_factors)

    # Minimize: (w - w_bench)' * Omega * (w - w_bench)
    # s.t. B' * w = target
    # For simplicity, use minimum variance neutral

    def objective(w):
        return w @ factor_cov @ w

    constraints = [
        {"type": "eq", "fun": lambda w: B.T @ w - target},
        {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
    ]

    bounds = [(min_weight, max_weight)] * n_assets
    x0 = np.ones(n_assets) / n_assets

    result = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 1000},
    )

    if result.success:
        return result.x
    else:
        # Return equal weight as fallback
        return np.ones(n_assets) / n_assets


def optimize_factor_portfolio(
    factor_df: pd.DataFrame,
    forward_returns: pd.Series,
    config: FactorOptimizerConfig | None = None,
    current_weights: np.ndarray | None = None,
) -> dict:
    """
    Full factor portfolio optimization pipeline.

    1. Compute IC for all factors
    2. Combine factors using specified method
    3. Generate factor timing signals
    4. Output portfolio weights and expected metrics
    """
    config = config or FactorOptimizerConfig()
    factor_names = factor_df.columns.tolist()

    # 1. Compute IC for all factors at configured horizon
    ic_results = compute_multi_factor_ic(
        factor_df, forward_returns, horizons=[config.ic_horizon]
    )
    ic_values = {r["factor_name"]: r["ic_pearson"] for _, r in ic_results.iterrows()}

    # 2. Compute factor returns for optimization
    if factor_names[0] in factor_df.columns:
        # Simple factor returns: factor value * next period return
        # In practice, would use factor-mimicking portfolios
        factor_returns_df = pd.DataFrame()
        for name in factor_names:
            factor_returns_df[name] = factor_df[name].shift(-1) * forward_returns
        factor_returns_df = factor_returns_df.dropna()
    else:
        factor_returns_df = None

    # 3. Combine factors
    weights = combine_factors(
        factor_df,
        config=config,
        ic_values=ic_values,
        factor_returns=factor_returns_df,
    )

    # 3.5 Apply turnover penalty if specified
    if config.turnover_penalty > 0 and current_weights is not None:
        turnover = np.sum(np.abs(weights - current_weights))
        penalty = config.turnover_penalty / 10000 * turnover
        # Adjust weights slightly toward current
        weights = weights * (1 - penalty) + current_weights * penalty
        weights = weights / weights.sum()

    # 4. Expected metrics
    expected_return = 0.0
    expected_risk = 0.0
    if factor_returns_df is not None:
        port_returns = factor_returns_df @ weights
        expected_return = port_returns.mean() * 252
        expected_risk = port_returns.std() * np.sqrt(252)

    return {
        "factor_names": factor_names,
        "weights": weights.tolist(),
        "ic_values": ic_values,
        "expected_return": expected_return,
        "expected_vol": expected_risk,
        "sharpe": expected_return / expected_risk if expected_risk > 0 else 0,
        "turnover": (
            float(np.sum(np.abs(weights - current_weights)))
            if current_weights is not None
            else 0
        ),
        "config_method": config.method.value,
    }


def generate_factor_timing_signal(
    factor_df: pd.DataFrame,
    factor_name: str,
    lookback: int = 60,
) -> pd.Series:
    """
    Generate timing signal for a factor based on its recent IC performance.

    Returns signal in [-1, 1] where:
    - 1 = factor performing well, increase allocation
    - -1 = factor performing poorly, decrease allocation
    """
    # Compute rolling IC
    # For now, use factor's own momentum as proxy
    from src.factors.factors import momentum_factor

    # Get factor's own IC time series (simplified)
    # In practice, would compute rolling IC
    signal = momentum_factor(factor_df[factor_name], lookback=lookback)

    # Normalize to [-1, 1]
    signal = signal.clip(-2, 2) / 2

    return signal.rename(f"{factor_name}_timing")


def dynamic_factor_allocation(
    factor_df: pd.DataFrame,
    forward_returns: pd.Series,
    base_config: FactorOptimizerConfig | None = None,
    rebalance_freq: int = 20,  # days
) -> pd.DataFrame:
    """
    Generate dynamic factor weights over time with rebalancing.

    Returns DataFrame of weights over time (dates x factors).
    """
    config = base_config or FactorOptimizerConfig()
    weights_history = []
    dates = []
    current_weights = None

    for i in range(config.ic_min_periods, len(factor_df), rebalance_freq):
        date = factor_df.index[i]

        # Use expanding window
        hist_factors = factor_df.iloc[:i]
        hist_returns = forward_returns.iloc[:i]

        result = optimize_factor_portfolio(
            hist_factors,
            hist_returns,
            config=config,
            current_weights=current_weights,
        )

        weights_history.append(result["weights"])
        dates.append(date)
        current_weights = np.array(result["weights"])

    return pd.DataFrame(weights_history, index=dates, columns=factor_df.columns)
