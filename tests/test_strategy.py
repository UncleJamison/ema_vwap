"""
Unit tests for Strategy Rules Engine.
"""

import pandas as pd

from src.config import StrategyParams
from src.data_loader import DataLoader
from src.strategy import EmaVwapStrategy


def test_crossover_strategy():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    params = StrategyParams(
        strategy_mode="crossover", vwap_slope_min=0.0, volume_filter_enabled=False
    )
    strategy = EmaVwapStrategy(params)
    df_signals = strategy.generate_signals(df)

    assert "signal" in df_signals.columns
    assert "raw_signal" in df_signals.columns
    # Check signal values are in {-1, 0, 1}
    assert set(df_signals["signal"].unique()).issubset({-1, 0, 1})


def test_multi_ema_strategy():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=100)
    params = StrategyParams(
        strategy_mode="multi_ema",
        fast_ema=8,
        slow_ema=21,
        trend_ema=50,
        volume_filter_enabled=False,
    )
    strategy = EmaVwapStrategy(params)
    df_signals = strategy.generate_signals(df)

    assert "signal" in df_signals.columns
    assert set(df_signals["signal"].unique()).issubset({-1, 0, 1})


def test_pullback_strategy():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=200)
    params = StrategyParams(
        strategy_mode="pullback",
        pullback_tolerance_pct=0.5,
        volume_filter_enabled=False,
    )
    strategy = EmaVwapStrategy(params)
    df_signals = strategy.generate_signals(df)

    assert "signal" in df_signals.columns
    assert set(df_signals["signal"].unique()).issubset({-1, 0, 1})


def test_slope_and_volume_filters():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)

    # Strict volume filter should filter out some raw signals
    params_strict = StrategyParams(
        strategy_mode="crossover",
        vwap_slope_min=0.0001,
        volume_filter_enabled=True,
        volume_multiplier=2.5,
    )
    strategy_strict = EmaVwapStrategy(params_strict)
    df_strict = strategy_strict.generate_signals(df)

    raw_signal_count = (df_strict["raw_signal"] != 0).sum()
    filtered_signal_count = (df_strict["signal"] != 0).sum()

    assert filtered_signal_count <= raw_signal_count


def test_long_only_trade_direction():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams(
        strategy_mode="crossover",
        trade_direction="long_only",
        vwap_slope_min=0.0,
        volume_filter_enabled=False,
    )
    strategy = EmaVwapStrategy(params)
    df_signals = strategy.generate_signals(df)

    # Should contain NO short signals (-1)
    assert (df_signals["signal"] == -1).sum() == 0
    assert (df_signals["signal"] == 1).sum() > 0


def test_regime_classification_in_static_modes():
    """Non-auto modes (crossover, multi_ema, pullback) must populate genuine regimes, not 'static'."""
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    for mode in ("crossover", "multi_ema", "pullback"):
        params = StrategyParams(strategy_mode=mode)
        strategy = EmaVwapStrategy(params)
        df_signals = strategy.generate_signals(df)

        assert "regime" in df_signals.columns
        assert (
            df_signals["regime"] != "static"
        ).all(), f"Mode {mode} should not have static regime"
        unique_regimes = set(df_signals["regime"].unique())
        assert unique_regimes.issubset(
            {"trending", "ranging", "high_vol", "insufficient_data"}
        )


def test_no_regime_lookahead_bias():
    """Verify point-in-time regime classification without lookahead bias."""
    # Part 1: First 150 bars strong linear uptrend
    closes_trend = [50000.0 + i * 50.0 for i in range(150)]
    # Part 2: Next 150 bars flat choppy range
    closes_flat = [
        closes_trend[-1] + (10.0 if i % 2 == 0 else -10.0) for i in range(150)
    ]
    all_closes = closes_trend + closes_flat

    dates = pd.date_range("2024-01-01", periods=300, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": all_closes,
            "high": [c + 15.0 for c in all_closes],
            "low": [c - 15.0 for c in all_closes],
            "close": all_closes,
            "volume": [1000.0] * 300,
        }
    )

    params = StrategyParams(strategy_mode="auto")
    strategy = EmaVwapStrategy(params)
    df_signals = strategy.generate_signals(df)

    # Early bars (after warmup, e.g. bar 80 to 140) should be 'trending'
    early_regimes = df_signals["regime"].iloc[80:140].unique()
    assert (
        "trending" in early_regimes
    ), f"Expected trending in early bars, got {early_regimes}"

    # Later bars (e.g. bar 250 to 300) should have changed, demonstrating point-in-time variation
    late_regimes = df_signals["regime"].iloc[250:300].unique()
    assert len(late_regimes) > 0
    assert (
        len(set(df_signals["regime"].unique())) > 1
    ), "Regime should vary over time, not be a constant scalar"


def test_auto_mode_signals_dynamic_routing():
    """Verify auto mode produces valid signals with per-bar regime assignment."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams(strategy_mode="auto", volume_filter_enabled=False)
    strategy = EmaVwapStrategy(params)
    df_signals = strategy.generate_signals(df)

    assert "signal" in df_signals.columns
    assert "regime" in df_signals.columns
    assert set(df_signals["signal"].unique()).issubset({-1, 0, 1})
