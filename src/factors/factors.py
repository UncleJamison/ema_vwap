"""
Alpha Factor Library (ema_vwap-pdt).

Pre-built alpha factors for systematic strategy research:
- Momentum (price, risk-adjusted)
- Mean Reversion (RSI, Z-score, Bollinger bands)
- Volume (OBV, volume-price trend, volume-weighted momentum)
- Volatility (realized vol, vol regime, vol risk premium)

Each factor is a callable that takes a DataFrame of OHLCV data and returns
a pandas Series of factor values (same index, aligned to close prices).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.indicators import compute_atr, compute_bollinger_bands, compute_rsi

# Type alias for factor functions
AlphaFactor = Callable[[pd.DataFrame], pd.Series]


@dataclass
class FactorMetadata:
    """Metadata describing an alpha factor."""

    name: str
    description: str
    category: str  # "momentum", "mean_reversion", "volume", "volatility"
    lookback: int
    params: dict
    frequency: str = "daily"  # "intraday", "daily", "weekly"


# =============================================================================
# Momentum Factors
# =============================================================================


def momentum_factor(
    df: pd.DataFrame,
    lookback: int = 20,
    skip: int = 1,
) -> pd.Series:
    """
    Price momentum: (Close_t / Close_{t-lookback-skip}) - 1

    Measures raw price change over lookback period, skipping most recent bar
    to avoid lookahead bias.
    """
    close = df["close"]
    return (close / close.shift(lookback + skip) - 1).rename("momentum")


def risk_adjusted_momentum(
    df: pd.DataFrame,
    lookback: int = 60,
    skip: int = 1,
    vol_lookback: int = 20,
) -> pd.Series:
    """
    Risk-adjusted momentum: momentum / rolling volatility.

    Normalizes momentum by realized volatility to get Sharpe-like measure.
    """
    mom = momentum_factor(df, lookback, skip)
    returns = df["close"].pct_change()
    vol = returns.rolling(vol_lookback).std() * np.sqrt(252)
    return (mom / (vol * np.sqrt(lookback / 252) + 1e-8)).rename("risk_adj_momentum")


def momentum_1m(df: pd.DataFrame) -> pd.Series:
    """1-month momentum (20 trading days)."""
    return momentum_factor(df, lookback=20).rename("mom_1m")


def momentum_3m(df: pd.DataFrame) -> pd.Series:
    """3-month momentum (60 trading days)."""
    return momentum_factor(df, lookback=60).rename("mom_3m")


def momentum_6m(df: pd.DataFrame) -> pd.Series:
    """6-month momentum (120 trading days)."""
    return momentum_factor(df, lookback=120).rename("mom_6m")


def momentum_12m(df: pd.DataFrame) -> pd.Series:
    """12-month momentum (252 trading days)."""
    return momentum_factor(df, lookback=252).rename("mom_12m")


def dual_momentum(
    df: pd.DataFrame,
    fast_lookback: int = 20,
    slow_lookback: int = 60,
) -> pd.Series:
    """
    Dual momentum: fast momentum - slow momentum.

    Positive when short-term momentum exceeds long-term trend.
    """
    fast = momentum_factor(df, fast_lookback)
    slow = momentum_factor(df, slow_lookback)
    return (fast - slow).rename("dual_momentum")


# =============================================================================
# Mean Reversion Factors
# =============================================================================


def mean_reversion_factor(
    df: pd.DataFrame,
    lookback: int = 20,
    method: str = "zscore",
) -> pd.Series:
    """
    Mean reversion signal based on distance from rolling mean.

    Methods:
    - "zscore": (Close - MA) / Std
    - "pct_from_ma": (Close / MA - 1)
    - "bollinger": Position within Bollinger bands
    """
    close = df["close"]

    if method == "zscore":
        ma = close.rolling(lookback).mean()
        std = close.rolling(lookback).std()
        return ((close - ma) / (std + 1e-8)).rename("mean_rev_zscore")

    elif method == "pct_from_ma":
        ma = close.rolling(lookback).mean()
        return ((close / ma) - 1).rename("mean_rev_pct")

    elif method == "bollinger":
        upper, _middle, lower = compute_bollinger_bands(close, lookback)
        # Position within bands: 0 = lower, 1 = upper
        position = (close - lower) / (upper - lower + 1e-8)
        # Mean reversion signal: inverse of position (high = overbought = negative signal)
        return (0.5 - position).rename("mean_rev_bollinger")

    raise ValueError(f"Unknown method: {method}")


def rsi_factor(
    df: pd.DataFrame,
    lookback: int = 14,
) -> pd.Series:
    """
    RSI-based mean reversion.

    Returns normalized RSI: (RSI - 50) / 50, so:
    - Positive = oversold (buy signal)
    - Negative = overbought (sell signal)
    """
    rsi = compute_rsi(df["close"], lookback)
    return ((50 - rsi) / 50).rename("rsi_mean_rev")


def bollinger_position(
    df: pd.DataFrame,
    lookback: int = 20,
    num_std: float = 2.0,
) -> pd.Series:
    """
    Position within Bollinger Bands.

    Returns value in [0, 1] where 0 = lower band, 1 = upper band.
    """
    close = df["close"]
    upper, _middle, lower = compute_bollinger_bands(close, lookback, num_std)
    return ((df["close"] - lower) / (upper - lower + 1e-8)).rename("bb_position")


def zscore_factor(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.Series:
    """Z-score of returns over lookback window."""
    returns = df["close"].pct_change()
    mean = returns.rolling(lookback).mean()
    std = returns.rolling(lookback).std()
    return ((returns - mean) / (std + 1e-8)).rename("returns_zscore")


# =============================================================================
# Volume Factors
# =============================================================================


def volume_factor(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.Series:
    """
    Volume trend: current volume / rolling average volume.

    > 1 = above average volume, < 1 = below average.
    """
    volume = df["volume"]
    avg_vol = volume.rolling(lookback).mean()
    return (volume / (avg_vol + 1e-8)).rename("volume_ratio")


def obv_factor(
    df: pd.DataFrame,
) -> pd.Series:
    """
    On-Balance Volume (OBV) normalized.

    OBV = cumulative sum of volume * sign(close change).
    Normalized by total volume for stationarity.
    """
    close = df["close"]
    volume = df["volume"]
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    total_vol = volume.rolling(252).sum()
    return (obv / (total_vol + 1e-8)).rename("obv_normalized")


def vpt_factor(
    df: pd.DataFrame,
) -> pd.Series:
    """
    Volume-Price Trend (VPT).

    VPT = cumulative sum of volume * pct_change(close).
    Normalized by average volume.
    """
    close = df["close"]
    volume = df["volume"]
    vpt = (volume * close.pct_change()).fillna(0).cumsum()
    avg_vol = volume.rolling(252).mean()
    return (vpt / (avg_vol + 1e-8)).rename("vpt_normalized")


def volume_momentum(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.Series:
    """
    Volume momentum: current volume / volume lookback periods ago.
    """
    volume = df["volume"]
    return (volume / volume.shift(lookback) - 1).rename("volume_momentum")


def volume_weighted_momentum(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.Series:
    """
    Volume-weighted momentum: price momentum weighted by volume ratio.

    High-volume momentum moves get more weight.
    """
    price_mom = momentum_factor(df, lookback)
    vol_ratio = volume_factor(df, lookback)
    return (price_mom * vol_ratio).rename("vol_weighted_momentum")


def dollar_volume_factor(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.Series:
    """
    Dollar volume trend: (close * volume) / rolling average.
    """
    dollar_vol = df["close"] * df["volume"]
    avg_dollar_vol = dollar_vol.rolling(lookback).mean()
    return (dollar_vol / (avg_dollar_vol + 1e-8)).rename("dollar_volume_ratio")


# =============================================================================
# Volatility Factors
# =============================================================================


def realized_volatility(
    df: pd.DataFrame,
    lookback: int = 20,
    annualize: bool = True,
) -> pd.Series:
    """
    Realized volatility from close-to-close returns.

    Annualized by default (sqrt(252) for daily data).
    """
    returns = df["close"].pct_change()
    vol = returns.rolling(lookback).std()
    if annualize:
        vol = vol * np.sqrt(252)
    return vol.rename("realized_vol")


def volatility_factor(
    df: pd.DataFrame,
    lookback: int = 20,
    annualize: bool = True,
) -> pd.Series:
    """
    Volatility factor: realized volatility as an alpha factor.

    Higher volatility can predict lower future returns (vol drag) or
    higher risk premium depending on market regime.
    """
    return realized_volatility(df, lookback, annualize).rename("volatility_factor")


def volatility_regime(
    df: pd.DataFrame,
    short_lookback: int = 10,
    long_lookback: int = 60,
) -> pd.Series:
    """
    Volatility regime: short-term vol / long-term vol.

    > 1 = high vol regime, < 1 = low vol regime.
    """
    short_vol = realized_volatility(df, short_lookback, annualize=False)
    long_vol = realized_volatility(df, long_lookback, annualize=False)
    return (short_vol / (long_vol + 1e-8)).rename("vol_regime")


def vol_risk_premium(
    df: pd.DataFrame,
    lookback: int = 20,
    implied_vol: pd.Series | None = None,
) -> pd.Series:
    """
    Volatility risk premium: implied vol - realized vol.

    If implied_vol not provided, uses ATM IV approximation from options.
    Returns positive when implied > realized (premium for selling vol).
    """
    realized = realized_volatility(df, lookback, annualize=True)
    if implied_vol is not None:
        return (implied_vol - realized).rename("vol_risk_premium")
    # Placeholder: return zero if no IV data
    return pd.Series(0.0, index=df.index, name="vol_risk_premium")


def atr_factor(
    df: pd.DataFrame,
    lookback: int = 14,
) -> pd.Series:
    """
    Average True Range (ATR) as volatility measure.

    Normalized by price for cross-asset comparison.
    """
    atr = compute_atr(df["high"], df["low"], df["close"], lookback)
    return (atr / df["close"]).rename("atr_pct")


def vol_skew_factor(
    df: pd.DataFrame,
    lookback: int = 60,
) -> pd.Series:
    """
    Volatility skew: skew of returns distribution.

    Negative skew = fat left tail (crash risk).
    """
    returns = df["close"].pct_change()
    return returns.rolling(lookback).skew().rename("returns_skew")


# =============================================================================
# Factor Computation Pipeline
# =============================================================================

# Registry of all available factors with metadata
FACTOR_REGISTRY: dict[str, tuple[AlphaFactor, FactorMetadata]] = {
    # Momentum
    "momentum_1m": (
        momentum_1m,
        FactorMetadata("momentum_1m", "1-month price momentum", "momentum", 20, {}),
    ),
    "momentum_3m": (
        momentum_3m,
        FactorMetadata("momentum_3m", "3-month price momentum", "momentum", 60, {}),
    ),
    "momentum_6m": (
        momentum_6m,
        FactorMetadata("momentum_6m", "6-month price momentum", "momentum", 120, {}),
    ),
    "momentum_12m": (
        momentum_12m,
        FactorMetadata("momentum_12m", "12-month price momentum", "momentum", 252, {}),
    ),
    "dual_momentum": (
        dual_momentum,
        FactorMetadata(
            "dual_momentum",
            "Fast - slow momentum",
            "momentum",
            60,
            {"fast": 20, "slow": 60},
        ),
    ),
    "risk_adj_momentum": (
        risk_adjusted_momentum,
        FactorMetadata(
            "risk_adj_momentum", "Risk-adjusted momentum", "momentum", 60, {}
        ),
    ),
    # Mean Reversion
    "mean_rev_zscore": (
        lambda df: mean_reversion_factor(df, method="zscore"),
        FactorMetadata(
            "mean_rev_zscore", "Z-score mean reversion", "mean_reversion", 20, {}
        ),
    ),
    "mean_rev_pct": (
        lambda df: mean_reversion_factor(df, method="pct_from_ma"),
        FactorMetadata(
            "mean_rev_pct", "% from MA mean reversion", "mean_reversion", 20, {}
        ),
    ),
    "mean_rev_bollinger": (
        lambda df: mean_reversion_factor(df, method="bollinger"),
        FactorMetadata(
            "mean_rev_bollinger",
            "Bollinger band mean reversion",
            "mean_reversion",
            20,
            {},
        ),
    ),
    "rsi_mean_rev": (
        rsi_factor,
        FactorMetadata("rsi_mean_rev", "RSI mean reversion", "mean_reversion", 14, {}),
    ),
    "bb_position": (
        bollinger_position,
        FactorMetadata(
            "bb_position", "Bollinger band position", "mean_reversion", 20, {}
        ),
    ),
    "returns_zscore": (
        zscore_factor,
        FactorMetadata("returns_zscore", "Returns Z-score", "mean_reversion", 20, {}),
    ),
    # Volume
    "volume_ratio": (
        volume_factor,
        FactorMetadata("volume_ratio", "Volume / avg volume", "volume", 20, {}),
    ),
    "obv_normalized": (
        obv_factor,
        FactorMetadata("obv_normalized", "Normalized OBV", "volume", 252, {}),
    ),
    "vpt_normalized": (
        vpt_factor,
        FactorMetadata("vpt_normalized", "Normalized VPT", "volume", 252, {}),
    ),
    "volume_momentum": (
        volume_momentum,
        FactorMetadata("volume_momentum", "Volume momentum", "volume", 20, {}),
    ),
    "vol_weighted_momentum": (
        volume_weighted_momentum,
        FactorMetadata(
            "vol_weighted_momentum", "Vol-weighted momentum", "volume", 20, {}
        ),
    ),
    "dollar_volume_ratio": (
        dollar_volume_factor,
        FactorMetadata("dollar_volume_ratio", "Dollar volume ratio", "volume", 20, {}),
    ),
    # Volatility
    "realized_vol": (
        realized_volatility,
        FactorMetadata("realized_vol", "Realized volatility", "volatility", 20, {}),
    ),
    "vol_regime": (
        volatility_regime,
        FactorMetadata("vol_regime", "Vol regime (short/long)", "volatility", 60, {}),
    ),
    "vol_risk_premium": (
        vol_risk_premium,
        FactorMetadata(
            "vol_risk_premium", "Vol risk premium (IV - RV)", "volatility", 20, {}
        ),
    ),
    "atr_pct": (
        atr_factor,
        FactorMetadata("atr_pct", "ATR as % of price", "volatility", 14, {}),
    ),
    "returns_skew": (
        vol_skew_factor,
        FactorMetadata("returns_skew", "Returns skewness", "volatility", 60, {}),
    ),
}


def compute_all_factors(
    df: pd.DataFrame,
    factor_names: list[str] | None = None,
) -> pd.DataFrame:
    """
    Compute multiple factors at once and return as DataFrame.

    Args:
        df: OHLCV DataFrame
        factor_names: List of factor names to compute (None = all)

    Returns:
        DataFrame with factor values (same index as input)
    """
    if factor_names is None:
        factor_names = list(FACTOR_REGISTRY.keys())

    results = {}
    for name in factor_names:
        if name not in FACTOR_REGISTRY:
            raise ValueError(f"Unknown factor: {name}")
        factor_fn, _metadata = FACTOR_REGISTRY[name]
        try:
            results[name] = factor_fn(df)
        except Exception as e:
            # Return NaN series on error
            results[name] = pd.Series(np.nan, index=df.index, name=name)
            print(f"[FactorEngine] Error computing {name}: {e}")

    return pd.DataFrame(results, index=df.index)


def get_factor_metadata(name: str) -> FactorMetadata:
    """Get metadata for a registered factor."""
    if name not in FACTOR_REGISTRY:
        raise ValueError(f"Unknown factor: {name}")
    return FACTOR_REGISTRY[name][1]


def list_factors(category: str | None = None) -> list[str]:
    """List available factor names, optionally filtered by category."""
    if category is None:
        return list(FACTOR_REGISTRY.keys())
    return [
        name for name, (_, meta) in FACTOR_REGISTRY.items() if meta.category == category
    ]


def get_factor_categories() -> list[str]:
    """Get list of all factor categories."""
    return sorted({meta.category for _, meta in FACTOR_REGISTRY.values()})
