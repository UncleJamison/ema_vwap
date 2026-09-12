"""
Unit tests for Strategy Rules Engine.
"""

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
