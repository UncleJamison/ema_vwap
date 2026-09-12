"""
Unit tests for Technical Indicators Engine.
"""

import pandas as pd

from src.config import StrategyParams
from src.data_loader import DataLoader
from src.indicators import (
    compute_all_indicators,
    compute_atr,
    compute_ema,
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
