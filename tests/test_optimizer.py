"""
Unit tests for Optuna Strategy Hyperparameter Optimizer.
"""

import pytest

from src.config import OptunaConfig, StrategyParams
from src.data_loader import DataLoader
from src.optimizer import OptunaOptimizer


def test_optuna_optimizer_basic():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    params = StrategyParams()
    opt_config = OptunaConfig(n_trials=5, target_metric="sharpe_ratio")

    optimizer = OptunaOptimizer(base_params=params, config=opt_config)
    res = optimizer.optimize(df)

    assert "best_value" in res
    assert "best_params" in res
    assert "param_importance" in res
    assert "top_trials" in res
    assert "best_backtest_result" in res
    assert len(res["top_trials"]) <= 5
    assert res["best_params"]["fast_ema"] >= 5


def test_optuna_optimizer_multi_ema_and_pullback():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)

    # Test Multi-EMA mode
    multi_params = StrategyParams(strategy_mode="multi_ema")
    opt_config = OptunaConfig(n_trials=4, target_metric="total_return_pct")
    multi_opt = OptunaOptimizer(base_params=multi_params, config=opt_config)
    res_multi = multi_opt.optimize(df)
    assert res_multi["best_params"]["slow_ema"] > res_multi["best_params"]["fast_ema"]
    assert res_multi["best_params"]["trend_ema"] > res_multi["best_params"]["slow_ema"]

    # Test Pullback mode
    pullback_params = StrategyParams(strategy_mode="pullback")
    pullback_opt = OptunaOptimizer(base_params=pullback_params, config=opt_config)
    res_pullback = pullback_opt.optimize(df)
    assert "pullback_tolerance_pct" in res_pullback["best_params"]


def test_optuna_optimizer_insufficient_data():
    df = DataLoader.generate_synthetic_candles(num_bars=20, seed=42)
    params = StrategyParams()
    optimizer = OptunaOptimizer(base_params=params)

    with pytest.raises(ValueError, match="minimum 50 bars required"):
        optimizer.optimize(df)


def test_optuna_optimizer_auto_mode():
    df = DataLoader.generate_synthetic_candles(num_bars=500, seed=42)
    params = StrategyParams(strategy_mode="auto")
    opt_config = OptunaConfig(n_trials=10, target_metric="total_return_pct")

    optimizer = OptunaOptimizer(base_params=params, config=opt_config)
    res = optimizer.optimize(df)

    assert "best_params" in res
    assert res["best_params"]["strategy_mode"] in [
        "crossover",
        "multi_ema",
        "pullback",
    ]
    assert len(res["top_trials"]) > 0
    assert any("strategy_mode" in t["params"] for t in res["top_trials"])


def test_optuna_optimizer_advanced_config():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    params = StrategyParams(fast_ema=12, slow_ema=26)
    opt_config = OptunaConfig(
        n_trials=6,
        n_startup_trials=3,
        seed_base_params=True,
        multivariate=True,
        target_metric="sharpe_ratio",
    )

    optimizer = OptunaOptimizer(base_params=params, config=opt_config)
    res = optimizer.optimize(df)

    assert "best_value" in res
    assert "best_params" in res
    assert len(res["top_trials"]) <= 6


def test_optuna_best_value_matches_reported_metric():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    opt_config = OptunaConfig(
        n_trials=5,
        min_trades=3,
        target_metric="total_return_pct",
    )

    result = OptunaOptimizer(StrategyParams(), opt_config).optimize(df)

    assert "best_value_valid" in result
    if result["best_value_valid"]:
        assert result["best_value"] == pytest.approx(
            result["best_metric_value"], abs=0.01
        )


def test_optuna_optimizer_parallel():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    params = StrategyParams()
    opt_config = OptunaConfig(
        n_trials=4,
        n_jobs=2,
        target_metric="net_profit",
    )

    optimizer = OptunaOptimizer(base_params=params, config=opt_config)
    res = optimizer.optimize(df)

    assert "best_value" in res
    assert "best_params" in res
    assert len(res["top_trials"]) <= 4


@pytest.mark.parametrize(
    "metric", ["drawdown_penalized_sharpe", "calmar_ratio", "sortino_ratio"]
)
def test_optuna_composite_metrics(metric):
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    params = StrategyParams()
    opt_config = OptunaConfig(
        n_trials=3,
        target_metric=metric,  # type: ignore
    )

    optimizer = OptunaOptimizer(base_params=params, config=opt_config)
    res = optimizer.optimize(df)

    assert res["target_metric"] == metric
    assert "best_value" in res
    assert "best_params" in res
    assert res["best_value"] is None or isinstance(res["best_value"], float)


def test_optuna_optimizer_static_delta_multivariate():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    multi_params = StrategyParams(strategy_mode="multi_ema")
    opt_config = OptunaConfig(
        n_trials=8,
        multivariate=True,
        seed_base_params=True,
        target_metric="total_return_pct",
    )
    multi_opt = OptunaOptimizer(base_params=multi_params, config=opt_config)
    res_multi = multi_opt.optimize(df)

    assert "slow_ema" in res_multi["best_params"]
    assert "trend_ema" in res_multi["best_params"]
    assert res_multi["best_params"]["slow_ema"] > res_multi["best_params"]["fast_ema"]
    assert res_multi["best_params"]["trend_ema"] > res_multi["best_params"]["slow_ema"]

    # Verify trial dictionary translation
    for trial_entry in res_multi["top_trials"]:
        if "slow_delta" in trial_entry["params"]:
            assert "slow_ema" in trial_entry["params"]
            assert (
                trial_entry["params"]["slow_ema"]
                == trial_entry["params"]["fast_ema"]
                + trial_entry["params"]["slow_delta"]
            )
        if "trend_delta" in trial_entry["params"]:
            assert "trend_ema" in trial_entry["params"]
            assert (
                trial_entry["params"]["trend_ema"]
                == trial_entry["params"]["slow_ema"]
                + trial_entry["params"]["trend_delta"]
            )
        if "slow_ema" in trial_entry["params"]:
            assert (
                opt_config.slow_ema_min
                <= trial_entry["params"]["slow_ema"]
                <= opt_config.slow_ema_max
            )
        if "trend_ema" in trial_entry["params"]:
            assert (
                opt_config.trend_ema_min
                <= trial_entry["params"]["trend_ema"]
                <= opt_config.trend_ema_max
            )


def test_optuna_optimizer_tunes_risk_per_trade():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    config = OptunaConfig(
        n_trials=3,
        seed_base_params=False,
        risk_per_trade_min=0.5,
        risk_per_trade_max=1.5,
    )

    result = OptunaOptimizer(StrategyParams(), config).optimize(df)

    assert all(
        config.risk_per_trade_min
        <= trial["params"]["risk_per_trade_pct"]
        <= config.risk_per_trade_max
        for trial in result["top_trials"]
    )


def test_optuna_optimizer_rejects_invalid_target_metric():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    config = OptunaConfig(target_metric="not_a_metric")  # type: ignore

    with pytest.raises(ValueError, match="Unsupported Optuna target metric"):
        OptunaOptimizer(StrategyParams(), config).optimize(df)


def test_optuna_optimizer_rejects_invalid_ranges():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    config = OptunaConfig(fast_ema_min=30, fast_ema_max=3)

    with pytest.raises(ValueError, match="Invalid fast_ema range"):
        OptunaOptimizer(StrategyParams(), config).optimize(df)


def test_optuna_optimizer_requires_valid_trial():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    config = OptunaConfig(n_trials=2, min_trades=10_000)

    result = OptunaOptimizer(StrategyParams(), config).optimize(df)

    assert result["best_value_valid"] is False
    assert result["best_value"] is None
