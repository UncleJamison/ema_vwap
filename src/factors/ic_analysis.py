"""
Information Coefficient (IC) Analysis (ema_vwap-pdt).

Provides:
- IC computation (Pearson, Spearman, Kendall)
- IC decay curves across horizons
- IC summary statistics (mean, std, IR, t-stat)
- Factor ranking by IC quality
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class ICResult:
    """Result of IC computation for a single factor."""

    factor_name: str
    horizon: int
    ic_pearson: float
    ic_spearman: float
    ic_kendall: float
    p_value_pearson: float
    p_value_spearman: float
    n_observations: int

    def to_dict(self) -> dict:
        return {
            "factor_name": self.factor_name,
            "horizon": self.horizon,
            "ic_pearson": round(self.ic_pearson, 6),
            "ic_spearman": round(self.ic_spearman, 6),
            "ic_kendall": round(self.ic_kendall, 6),
            "p_value_pearson": round(self.p_value_pearson, 6),
            "p_value_spearman": round(self.p_value_spearman, 6),
            "n_observations": self.n_observations,
        }


@dataclass
class ICSeriesResult:
    """IC time series for a factor across multiple dates."""

    factor_name: str
    horizon: int
    dates: pd.DatetimeIndex
    ic_values: pd.Series  # IC at each date
    mean_ic: float
    std_ic: float
    ir: float  # Information Ratio = mean_ic / std_ic
    t_stat: float
    hit_rate: float  # % of periods with IC > 0
    max_drawdown: float

    def to_dict(self) -> dict:
        return {
            "factor_name": self.factor_name,
            "horizon": self.horizon,
            "mean_ic": round(self.mean_ic, 6),
            "std_ic": round(self.std_ic, 6),
            "ir": round(self.ir, 6),
            "t_stat": round(self.t_stat, 6),
            "hit_rate": round(self.hit_rate, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "n_periods": len(self.ic_values),
        }


def compute_ic(
    factor_values: pd.Series,
    forward_returns: pd.Series,
    method: str = "pearson",
) -> tuple[float, float]:
    """
    Compute Information Coefficient between factor and forward returns.

    Args:
        factor_values: Factor values at time t
        forward_returns: Returns from t to t+horizon
        method: "pearson", "spearman", or "kendall"

    Returns:
        (ic_value, p_value)
    """
    # Align and drop NaN
    aligned = pd.concat([factor_values, forward_returns], axis=1, join="inner").dropna()
    if len(aligned) < 3:
        return 0.0, 1.0

    x = aligned.iloc[:, 0]
    y = aligned.iloc[:, 1]

    if method == "pearson":
        ic, p = stats.pearsonr(x, y)
    elif method == "spearman":
        ic, p = stats.spearmanr(x, y)
    elif method == "kendall":
        ic, p = stats.kendalltau(x, y)
    else:
        raise ValueError(f"Unknown method: {method}")

    return float(ic), float(p)


def compute_ic_series(
    factor_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    horizon: int,
    method: str = "pearson",
    min_periods: int = 20,
) -> pd.Series:
    """
    Compute rolling IC time series for a factor.

    Args:
        factor_df: DataFrame of factor values (dates x factors)
        returns_df: DataFrame of forward returns (dates x assets)
        horizon: Forward return horizon in periods
        method: Correlation method
        min_periods: Minimum observations for valid IC

    Returns:
        Series of IC values indexed by date
    """
    # Compute forward returns if not provided
    # For now, assume returns_df already contains forward returns

    ic_values = {}
    common_dates = factor_df.index.intersection(returns_df.index)

    for date in common_dates:
        if date not in factor_df.index or date not in returns_df.index:
            continue

        # Cross-sectional IC at this date
        factor_slice = factor_df.loc[date].dropna()
        ret_slice = returns_df.loc[date].dropna()

        common_assets = factor_slice.index.intersection(ret_slice.index)
        if len(common_assets) < min_periods:
            ic_values[date] = np.nan
            continue

        x = factor_slice[common_assets]
        y = returns_df.loc[date, common_assets]

        if method == "pearson":
            ic, _ = stats.pearsonr(x, y)
        elif method == "spearman":
            ic, _ = stats.spearmanr(x, y)
        elif method == "kendall":
            ic, _ = stats.kendalltau(x, y)
        else:
            ic = np.nan

        ic_values[date] = ic

    return pd.Series(ic_values, name=f"ic_h{horizon}")


def compute_ic_decay(
    factor_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    horizons: list[int],
    method: str = "pearson",
    min_periods: int = 20,
) -> pd.DataFrame:
    """
    Compute IC decay curve across multiple horizons.

    Args:
        factor_df: Factor values (dates x assets)
        returns_df: Forward returns for each horizon (horizons x dates x assets)
                    OR single returns DataFrame with horizon column
        horizons: List of forward horizons to test
        method: Correlation method
        min_periods: Minimum cross-sectional observations

    Returns:
        DataFrame with columns: horizon, mean_ic, std_ic, ir, t_stat, hit_rate
    """
    results = []

    for h in horizons:
        # Get returns for this horizon
        if isinstance(returns_df, dict):
            ret_h = returns_df.get(h)
            if ret_h is None:
                continue
        elif isinstance(returns_df, pd.DataFrame) and "horizon" in returns_df.columns:
            ret_h = returns_df[returns_df["horizon"] == h].drop("horizon", axis=1)
        else:
            # Assume returns_df is already for this horizon
            ret_h = returns_df

        ic_series = compute_ic_series(factor_df, ret_h, h, method, min_periods)
        ic_clean = ic_series.dropna()

        if len(ic_clean) == 0:
            results.append(
                {
                    "horizon": h,
                    "mean_ic": 0.0,
                    "std_ic": 0.0,
                    "ir": 0.0,
                    "t_stat": 0.0,
                    "hit_rate": 0.0,
                    "max_drawdown": 0.0,
                    "n_periods": 0,
                }
            )
            continue

        mean_ic = ic_clean.mean()
        std_ic = ic_clean.std()
        ir = mean_ic / std_ic if std_ic > 0 else 0.0
        t_stat = mean_ic / (std_ic / np.sqrt(len(ic_clean))) if std_ic > 0 else 0.0
        hit_rate = (ic_clean > 0).mean()

        # IC drawdown
        cum_ic = ic_clean.cumsum()
        running_max = cum_ic.cummax()
        max_dd = (cum_ic - running_max).min()

        results.append(
            {
                "horizon": h,
                "mean_ic": mean_ic,
                "std_ic": std_ic,
                "ir": ir,
                "t_stat": t_stat,
                "hit_rate": hit_rate,
                "max_drawdown": max_dd,
                "n_periods": len(ic_clean),
            }
        )

    return pd.DataFrame(results)


def ic_summary(
    ic_series: pd.Series,
    factor_name: str = "",
    horizon: int = 1,
) -> ICSeriesResult:
    """
    Compute comprehensive IC statistics for a factor.

    Args:
        ic_series: Time series of IC values
        factor_name: Name of the factor
        horizon: Forward horizon

    Returns:
        ICSeriesResult with comprehensive statistics
    """
    ic_clean = ic_series.dropna()

    if len(ic_clean) == 0:
        return ICSeriesResult(
            factor_name=factor_name,
            horizon=horizon,
            dates=pd.DatetimeIndex([]),
            ic_values=pd.Series(dtype=float),
            mean_ic=0.0,
            std_ic=0.0,
            ir=0.0,
            t_stat=0.0,
            hit_rate=0.0,
            max_drawdown=0.0,
        )

    mean_ic = ic_clean.mean()
    std_ic = ic_clean.std()
    ir = mean_ic / std_ic if std_ic > 0 else 0.0
    t_stat = mean_ic / (std_ic / np.sqrt(len(ic_clean))) if std_ic > 0 else 0.0
    hit_rate = (ic_clean > 0).mean()

    # Max drawdown
    cum_ic = ic_clean.cumsum()
    running_max = cum_ic.cummax()
    max_dd = (cum_ic - running_max).min()

    return ICSeriesResult(
        factor_name=factor_name,
        horizon=horizon,
        dates=ic_clean.index,
        ic_values=ic_clean,
        mean_ic=mean_ic,
        std_ic=std_ic,
        ir=ir,
        t_stat=t_stat,
        hit_rate=hit_rate,
        max_drawdown=max_dd,
    )


def rank_factors_by_ic(
    ic_results: list[ICResult] | pd.DataFrame,
    metric: str = "ir",
    ascending: bool = False,
) -> pd.DataFrame:
    """
    Rank factors by IC quality metric.

    Args:
        ic_results: List of ICResult or DataFrame with IC statistics
        metric: "ir", "mean_ic", "t_stat", "hit_rate"
        ascending: Sort order

    Returns:
        DataFrame ranked by metric
    """
    if isinstance(ic_results, list):
        df = pd.DataFrame([r.to_dict() for r in ic_results])
    else:
        df = ic_results.copy()

    if metric not in df.columns:
        raise ValueError(f"Metric {metric} not in results")

    return df.sort_values(metric, ascending=ascending).reset_index(drop=True)


def compute_multi_factor_ic(
    factor_df: pd.DataFrame,
    forward_returns: pd.Series,
    horizons: list[int] | None = None,
) -> pd.DataFrame:
    """
    Compute IC for all factors in a DataFrame at multiple horizons.

    Args:
        factor_df: DataFrame of factors (dates x factors)
        forward_returns: Series of forward returns (date -> return)
        horizons: List of horizons (if None, uses [1, 5, 10, 20])

    Returns:
        DataFrame with columns: factor, horizon, ic_pearson, ic_spearman, p_value
    """
    if horizons is None:
        horizons = [1, 5, 10, 20, 60]

    results = []
    factor_cols = factor_df.columns.tolist()

    for factor in factor_cols:
        factor_series = factor_df[factor]

        for h in horizons:
            # Get h-period forward returns
            ret_h = forward_returns.shift(-h)

            ic, p = compute_ic(factor_series, ret_h, method="pearson")
            ic_s, p_s = compute_ic(factor_series, ret_h, method="spearman")
            ic_k, _ = compute_ic(factor_series, ret_h, method="kendall")

            results.append(
                ICResult(
                    factor_name=factor,
                    horizon=h,
                    ic_pearson=ic,
                    ic_spearman=ic_s,
                    ic_kendall=ic_k,
                    p_value_pearson=p,
                    p_value_spearman=p_s,
                    n_observations=len(
                        pd.concat([factor_series, ret_h], axis=1).dropna()
                    ),
                )
            )

    return pd.DataFrame([r.to_dict() for r in results])


def factor_returns(
    factor_df: pd.DataFrame,
    forward_returns: pd.Series,
    quantiles: int = 5,
    horizon: int = 1,
) -> pd.DataFrame:
    """
    Compute factor quintile returns for factor validation.

    Args:
        factor_df: Factor values (dates x factors)
        forward_returns: Forward returns series
        quantiles: Number of quantile buckets
        horizon: Forward horizon

    Returns:
        DataFrame with mean returns per quantile per factor
    """
    ret_h = forward_returns.shift(-horizon)
    results = []

    for factor in factor_df.columns:
        factor_series = factor_df[factor]

        for date in factor_series.index:
            if date not in ret_h.index:
                continue

            f_val = factor_series.loc[date]
            ret_val = ret_h.loc[date]

            if pd.isna(f_val) or pd.isna(ret_val):
                continue

            # Cross-sectional quantile
            try:
                q = pd.qcut(f_val.dropna(), quantiles, labels=False, duplicates="drop")
                q_ret = pd.DataFrame({"quantile": q, "return": ret_val.loc[q.index]})
                q_means = q_ret.groupby("quantile")["return"].mean()

                for q_idx, mean_ret in q_means.items():
                    results.append(
                        {
                            "factor": factor,
                            "date": date,
                            "horizon": horizon,
                            "quantile": int(q_idx),
                            "mean_return": mean_ret,
                        }
                    )
            except ValueError:
                continue

    return pd.DataFrame(results)
