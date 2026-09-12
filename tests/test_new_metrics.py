#!/usr/bin/env python3
"""
Test script for new metrics implementation.
"""

import sys

import numpy as np
import pandas as pd

from src.backtester import BacktestEngine
from src.config import OptunaConfig, StrategyParams


def test_metrics_calculation():
    """Test that new metrics are calculated without errors."""

    print("Testing new metrics calculation...")

    # Create synthetic data for testing
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=500, freq="5min")
    data = {
        "timestamp": dates.astype(str),
        "open": 100 + np.cumsum(np.random.randn(500) * 0.5),
        "high": 101 + np.cumsum(np.random.randn(500) * 0.5),
        "low": 99 + np.cumsum(np.random.randn(500) * 0.5),
        "close": 100 + np.cumsum(np.random.randn(500) * 0.5),
        "volume": np.random.uniform(1000, 5000, 500),
    }
    df = pd.DataFrame(data)

    # Create test params
    params = StrategyParams(
        strategy_mode="crossover",
        fast_ema=9,
        slow_ema=21,
        trend_ema=50,
    )

    # Run backtest
    engine = BacktestEngine(params)
    result = engine.run(df)

    # Check that new metrics exist and are finite
    print(f"✓ drawdown_penalized_sortino: {result.drawdown_penalized_sortino}")
    assert hasattr(
        result, "drawdown_penalized_sortino"
    ), "Missing drawdown_penalized_sortino metric"
    assert isinstance(
        result.drawdown_penalized_sortino, (int, float)
    ), "drawdown_penalized_sortino should be numeric"
    assert np.isfinite(
        result.drawdown_penalized_sortino
    ), "drawdown_penalized_sortino should be finite"

    print(f"✓ ulcer_index: {result.ulcer_index}")
    assert hasattr(result, "ulcer_index"), "Missing ulcer_index metric"
    assert isinstance(result.ulcer_index, (int, float)), "ulcer_index should be numeric"
    assert np.isfinite(result.ulcer_index), "ulcer_index should be finite"

    print(f"✓ max_drawdown_pct: {result.max_drawdown_pct}")
    assert hasattr(result, "max_drawdown_pct"), "Missing max_drawdown_pct metric"

    print(f"✓ sortino_ratio: {result.sortino_ratio}")
    assert hasattr(result, "sortino_ratio"), "Missing sortino_ratio metric"

    print("\n✅ All metrics calculated successfully!")


def test_optuna_config():
    """Test that OptunaConfig includes new metrics."""

    print("Testing OptunaConfig...")

    config = OptunaConfig()

    # Check that new metrics are in target_metric options
    print(
        f"✓ Config has enable_hard_drawdown_constraint: {config.enable_hard_drawdown_constraint}"
    )
    assert hasattr(
        config, "enable_hard_drawdown_constraint"
    ), "Missing enable_hard_drawdown_constraint"

    print(
        f"✓ Config has max_drawdown_constraint_pct: {config.max_drawdown_constraint_pct}"
    )
    assert hasattr(
        config, "max_drawdown_constraint_pct"
    ), "Missing max_drawdown_constraint_pct"

    print(f"✓ Config has enable_multi_objective: {config.enable_multi_objective}")
    assert hasattr(config, "enable_multi_objective"), "Missing enable_multi_objective"

    print(f"✓ Config has multi_objective_metrics: {config.multi_objective_metrics}")
    assert hasattr(config, "multi_objective_metrics"), "Missing multi_objective_metrics"

    # Test that new metrics can be selected
    config.target_metric = "drawdown_penalized_sortino"
    print("✓ Can set target_metric to drawdown_penalized_sortino")

    config.target_metric = "ulcer_index"
    print("✓ Can set target_metric to ulcer_index")

    print("\n✅ OptunaConfig test passed!")


def test_multi_objective_config():
    """Test multi-objective configuration."""

    print("Testing multi-objective configuration...")

    config = OptunaConfig(
        enable_multi_objective=True,
        multi_objective_metrics=["sharpe_ratio", "max_drawdown_pct", "ulcer_index"],
    )

    print(f"✓ Multi-objective enabled: {config.enable_multi_objective}")
    assert config.enable_multi_objective, "Multi-objective should be enabled"

    print(f"✓ Multi-objective metrics: {config.multi_objective_metrics}")
    assert len(config.multi_objective_metrics) == 3, "Should have 3 objectives"
    assert "sharpe_ratio" in config.multi_objective_metrics
    assert "max_drawdown_pct" in config.multi_objective_metrics
    assert "ulcer_index" in config.multi_objective_metrics

    print("\n✅ Multi-objective config test passed!")


if __name__ == "__main__":
    try:
        test_metrics_calculation()
        test_optuna_config()
        test_multi_objective_config()
        print("\n" + "=" * 50)
        print("✅ ALL TESTS PASSED!")
        print("=" * 50)
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ TEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
