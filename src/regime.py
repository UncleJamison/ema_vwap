"""
Regime Detection Module (bead ema_vwap-0tj).

Three-regime classifier using industry-standard indicators:
  - ADX(14)     — trend strength (Wilder 1978)
  - ER(10)      — Efficient Ratio / Kaufman Kama (Kaufman 1995)
  - ATR%(14)    — normalized volatility vs 60-day median

Config-driven mapping to strategy_mode:
  trending  → multi_ema
  ranging   → pullback
  high_vol  → crossover (reduced position sizing in config/launcher)

3-bar hysteresis prevents whipsaw regime switches around ADX=25.
No external TA libraries — only pandas/numpy (already in requirements).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

# Regime -> strategy_mode mapping (consumed by BacktestEngine/StrategyParams/auto mode)
REGIME_MAP: dict[str, str] = {
    "trending": "multi_ema",
    "ranging": "pullback",
    "high_vol": "crossover",
}

RegimeLabel = Literal["trending", "ranging", "high_vol"]

# Sentinel returned when not enough history has elapsed to classify.
INSUFFICIENT_DATA: str = "insufficient_data"


@dataclass
class RegimeConfig:
    """Tunable thresholds for the three-regime classifier.

    Industry-standard defaults; swept by Optuna during batch optimization.
    """
    adx_threshold: float = 25.0      # ADX(14) above this = "has trend"
    er_threshold: float = 0.5        # ER(10) above this = "efficient trend"
    atr_multiplier: float = 1.5      # ATR% >= multiplier * 60-day median = high vol
    adx_period: int = 14
    er_period: int = 10
    atr_period: int = 14
    atr_median_window: int = 60      # rolling median window for ATR% baseline
    hysteresis_bars: int = 3         # consecutive bars required to flip regime


# ---------------------------------------------------------------------------
# Indicator computation (vectorized, no external deps)
# ---------------------------------------------------------------------------

def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average Directional Index (ADX) following Wilder's method.

    Requires 'high', 'low', 'close' columns. First `2*period-1` rows are NaN.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    high_low = high - low
    high_prev_close = (high - close.shift(1)).abs()
    low_prev_close = (low - close.shift(1)).abs()
    tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)

    # Wilder's smoothing via EMA with alpha = 1/period
    atr_smoothed = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    # Directional Movement
    up_move = high.diff()
    down_move = low.diff() * -1.0

    dm_plus = pd.Series(np.where(
        (up_move > down_move) & (up_move > 0), up_move, 0.0
    ), index=df.index)
    dm_minus = pd.Series(np.where(
        (down_move > up_move) & (down_move > 0), down_move, 0.0
    ), index=df.index)

    di_plus = dm_plus.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_smoothed
    di_minus = dm_minus.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_smoothed

    dx = (
        (di_plus - di_minus).abs()
        / (di_plus + di_minus)
    ) * 100.0
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx


def compute_efficient_ratio(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """Kaufman's Efficient Ratio over `period` bars.

    ER = |Close_t - Close_{t-period}| / sum(|close.diff()|)
    1.0 = perfectly efficient (linear), 0.0 = pure noise / chop.
    NaN for the first `period` bars.
    """
    close = df["close"]
    net_change = (close - close.shift(period)).abs()
    total_change = close.diff().abs().rolling(window=period).sum()
    er = net_change / total_change.replace(0, np.nan)
    return er


def compute_atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR as a percentage of close price: ATR(14) / close * 100.

    Uses the same TR computation as compute_adx. NaN for the first `period` bars.
    """
    from src.indicators import compute_atr

    atr = compute_atr(df, period=period)
    return (atr / df["close"].replace(0, np.nan)) * 100.0


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def classify_regime(
    df: pd.DataFrame, config: RegimeConfig | None = None
) -> pd.Series:
    """Vectorized per-bar regime classification.

    Returns a pandas Series of regime labels ('trending', 'ranging', 'high_vol')
    aligned to df.index. Bars with insufficient warmup data receive the label
    INSUFFICIENT_DATA.

    Classification logic (per bar):
      1. adx >= adx_threshold  → trending
      2. adx <  adx_threshold AND atr_pct >= atr_multiplier * median(atr_pct) → high_vol
      3. adx <  adx_threshold AND atr_pct <  atr_multiplier * median(atr_pct) → ranging

    The Efficient Ratio disambiguates "trending" vs "low-efficiency trending":
    if adx >= threshold but ER < er_threshold, the trend is noisy → demote to
    "high_vol" when volatility is elevated, otherwise "ranging".
    """
    config = config or RegimeConfig()

    adx = compute_adx(df, period=config.adx_period)
    er = compute_efficient_ratio(df, period=config.er_period)
    atr_pct = compute_atr_pct(df, period=config.atr_period)

    # Rolling median baseline for ATR% (volatility clustering)
    atr_med = atr_pct.rolling(window=config.atr_median_window, min_periods=config.atr_period).median()

    # Warmup: first row where all indicators are valid
    valid_from_idx = max(
        config.adx_period * 2 - 1,
        config.er_period,
        config.atr_period,
        config.atr_period + config.atr_median_window,
    )

    raw = pd.Series(INSUFFICIENT_DATA, index=df.index, dtype=object)

    mask_valid = adx.notna() & er.notna() & atr_pct.notna() & atr_med.notna()
    mask_valid &= np.arange(len(df)) >= valid_from_idx

    # Trending: high ADX AND efficient
    trending_cond = (adx >= config.adx_threshold) & (er >= config.er_threshold)
    # High vol: low ADX (or inefficient trend) + elevated ATR%
    high_vol_cond = (
        ((adx < config.adx_threshold) | (er < config.er_threshold))
        & (atr_pct >= config.atr_multiplier * atr_med)
    )
    # Ranging: low ADX + low ATR%
    ranging_cond = (
        (adx < config.adx_threshold)
        & (er < config.er_threshold)
        & (atr_pct < config.atr_multiplier * atr_med)
    )

    raw[mask_valid & trending_cond] = "trending"
    raw[mask_valid & ~trending_cond & high_vol_cond] = "high_vol"
    raw[mask_valid & ~trending_cond & ~high_vol_cond & ranging_cond] = "ranging"
    # Any remaining valid bars (shouldn't happen) default to ranging
    raw[mask_valid & raw.isin([INSUFFICIENT_DATA])] = "ranging"

    # Apply hysteresis to smooth transitions
    result = apply_hysteresis(raw, config.hysteresis_bars)
    return result


def apply_hysteresis(
    regime_series: pd.Series, confirmed_bars: int = 3
) -> pd.Series:
    """Apply hysteresis to regime switches.

    A regime label only flips after `confirmed_bars` consecutive bars agree on
    the new regime. INSUFFICIENT_DATA labels are preserved as-is.
    """
    result = regime_series.copy()
    regimes = [r for r in regime_series.unique() if r not in (INSUFFICIENT_DATA, np.nan)]
    if not regimes:
        return result

    last_confirmed = None
    run_length = 0
    last_value = None

    for idx in regime_series.index:
        value = regime_series.loc[idx]
        is_insufficient = value == INSUFFICIENT_DATA or pd.isna(value)
        if is_insufficient:
            result.loc[idx] = last_confirmed if last_confirmed is not None else INSUFFICIENT_DATA
            continue

        if last_confirmed is None:
            last_confirmed = value
            last_value = value
            run_length = 1
        elif value == last_value:
            run_length += 1
        else:
            last_value = value
            run_length = 1

        if run_length >= confirmed_bars:
            last_confirmed = value
            run_length = confirmed_bars

        result.loc[idx] = last_confirmed

    return result


def get_current_regime(
    df: pd.DataFrame, config: RegimeConfig | None = None
) -> str:
    """Return the regime label of the latest valid bar (wrapper for live use)."""
    series = classify_regime(df, config=config)
    last = series.iloc[-1]
    if last in (INSUFFICIENT_DATA, np.nan, None):
        return INSUFFICIENT_DATA
    return str(last)


def regime_to_strategy_mode(regime: str) -> str:
    """Map a regime label to a strategy_mode string for StrategyParams."""
    return REGIME_MAP.get(regime, "crossover")
