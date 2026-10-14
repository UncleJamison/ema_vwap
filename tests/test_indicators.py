"""
Unit tests for Technical Indicators Engine.
"""

import pandas as pd

from src.config import StrategyParams, get_vwap_slope_bounds
from src.data_loader import DataLoader
from src.indicators import (
    compute_all_indicators,
    compute_atr,
    compute_ema,
    compute_slope_bounds_from_df,
    compute_vwap,
    compute_vwap_slope,
)


def test_compute_ema():
    series = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    ema_9 = compute_ema(series, period=9)
    assert len(ema_9) == len(series)
    assert not ema_9.isna().any()
    assert ema_9.iloc[-1] > ema_9.iloc[0]


def test_compute_vwap():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    vwap = compute_vwap(df)
    assert len(vwap) == len(df)
    assert not vwap.isna().any()
    # VWAP should lie reasonably close to close prices
    assert abs(vwap.mean() - df["close"].mean()) / df["close"].mean() < 0.05


def test_compute_vwap_slope():
    vwap_series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0])
    slope = compute_vwap_slope(vwap_series, lookback=3)
    assert len(slope) == len(vwap_series)
    # Check positive slope for upward vwap
    assert slope.iloc[-1] > 0


def test_compute_atr():
    df = DataLoader.generate_synthetic_candles(num_bars=50, seed=42)
    atr = compute_atr(df, period=14)
    assert len(atr) == len(df)
    assert (atr > 0).all()


def test_compute_all_indicators():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    params = StrategyParams(fast_ema=9, slow_ema=21, trend_ema=50)
    df_ind = compute_all_indicators(df, params)

    assert "ema_9" in df_ind.columns
    assert "ema_21" in df_ind.columns
    assert "ema_50" in df_ind.columns
    assert "vwap" in df_ind.columns
    assert "vwap_slope" in df_ind.columns
    assert "atr" in df_ind.columns
    assert "rvol" in df_ind.columns


def test_compute_slope_bounds_from_df_normal():
    """Derives live p01 and p95 bounds from synthetic candle data when samples >= min_samples."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    bounds = compute_slope_bounds_from_df(df, timeframe="5m", min_samples=50)

    assert "vwap_slope_min" in bounds
    assert "vwap_slope_max" in bounds
    assert isinstance(bounds["vwap_slope_min"], float)
    assert isinstance(bounds["vwap_slope_max"], float)
    assert bounds["vwap_slope_min"] >= 1e-7
    assert bounds["vwap_slope_max"] <= 0.5
    assert bounds["vwap_slope_max"] > bounds["vwap_slope_min"]


def test_compute_slope_bounds_from_df_fallback_insufficient_samples():
    """Falls back to static get_vwap_slope_bounds when df has fewer than min_samples valid slope values."""
    df = DataLoader.generate_synthetic_candles(num_bars=30, seed=42)
    static_expected = get_vwap_slope_bounds("15m")
    bounds = compute_slope_bounds_from_df(df, timeframe="15m", min_samples=100)

    assert bounds == static_expected


def test_compute_slope_bounds_from_df_empty_or_none():
    """Returns static fallback safely without exception when df is empty or None."""
    static_expected = get_vwap_slope_bounds("1h")
    assert (
        compute_slope_bounds_from_df(pd.DataFrame(), timeframe="1h") == static_expected
    )
    assert compute_slope_bounds_from_df(None, timeframe="1h") == static_expected


def test_compute_slope_bounds_from_df_existing_vwap_column():
    """Reuses existing vwap column in df when present instead of recomputing."""
    df = DataLoader.generate_synthetic_candles(num_bars=250, seed=123)
    df["vwap"] = compute_vwap(df)
    bounds = compute_slope_bounds_from_df(df, timeframe="5m", min_samples=50)

    assert bounds["vwap_slope_max"] > bounds["vwap_slope_min"]


def test_compute_slope_bounds_from_df_flat_prices():
    """When all prices are flat (all slopes are zero/filtered), safely falls back to static bounds."""
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    df["close"] = 100.0
    df["high"] = 100.0
    df["low"] = 100.0
    df["open"] = 100.0
    static_expected = get_vwap_slope_bounds("5m")
    bounds = compute_slope_bounds_from_df(df, timeframe="5m", min_samples=50)

    assert bounds == static_expected
