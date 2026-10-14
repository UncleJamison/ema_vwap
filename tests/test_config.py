"""Tests for src/config.py — StrategyParams, OptunaConfig, WFOConfig, MonteCarloConfig, ExchangeConfig."""

from src.config import (
    ExchangeConfig,
    MonteCarloConfig,
    OptunaConfig,
    StrategyParams,
    WFOConfig,
)


def test_strategy_params_defaults():
    p = StrategyParams()
    assert p.strategy_mode == "crossover"
    assert p.fast_ema == 9
    assert p.slow_ema == 21
    assert p.initial_capital == 10000.0
    assert p.trade_direction == "long_only"
    assert p.asset_type == "crypto"
    assert p.session_mode == "crypto"


def test_strategy_params_custom():
    p = StrategyParams(
        strategy_mode="multi_ema",
        fast_ema=7,
        slow_ema=25,
        initial_capital=5000.0,
        risk_per_trade_pct=0.5,
    )
    assert p.strategy_mode == "multi_ema"
    assert p.fast_ema == 7
    assert p.slow_ema == 25
    assert p.initial_capital == 5000.0
    assert p.risk_per_trade_pct == 0.5


def test_strategy_params_to_dict_round_trip():
    original = StrategyParams(strategy_mode="pullback", fast_ema=5, slow_ema=15)
    d = original.to_dict()
    restored = StrategyParams(**d)
    assert restored.strategy_mode == original.strategy_mode
    assert restored.fast_ema == original.fast_ema
    assert restored.slow_ema == original.slow_ema


def test_strategy_params_from_dict_unknown_keys_ignored():
    d = StrategyParams().to_dict()
    d["unknown_key"] = "nope"
    restored = StrategyParams.from_dict(d)
    assert restored.strategy_mode == "crossover"


def test_strategy_params_from_dict_custom():
    d = {"strategy_mode": "crossover", "fast_ema": 12, "slow_ema": 26}
    restored = StrategyParams.from_dict(d)
    assert restored.strategy_mode == "crossover"
    assert restored.fast_ema == 12
    assert restored.slow_ema == 26


def test_exchange_config_defaults():
    cfg = ExchangeConfig()
    assert cfg.primary_exchange == "gemini"
    assert cfg.default_symbol == "BTC/USD"
    assert cfg.default_timeframe == "5m"
    assert cfg.limit == 500


def test_optuna_config_defaults():
    cfg = OptunaConfig()
    assert cfg.target_metric == "sharpe_ratio"
    assert cfg.n_trials == 50
    assert cfg.multivariate is True
    assert cfg.n_jobs == 1
    assert cfg.sampler_seed == 42
    assert cfg.regime_sweep_enabled is False


def test_optuna_config_to_dict_round_trip():
    original = OptunaConfig(n_trials=30, timeout_seconds=120, sampler_seed=123)
    d = original.to_dict()
    restored = OptunaConfig(**d)
    assert restored.n_trials == original.n_trials
    assert restored.timeout_seconds == original.timeout_seconds
    assert restored.sampler_seed == 123


def test_wfo_config_defaults():
    cfg = WFOConfig()
    assert cfg.num_windows == 5
    assert cfg.in_sample_ratio == 0.7
    assert cfg.window_type == "rolling"
    assert cfg.trials_per_window == 30


def test_wfo_config_custom():
    cfg = WFOConfig(num_windows=10, in_sample_ratio=0.8, window_type="anchored")
    assert cfg.num_windows == 10
    assert cfg.in_sample_ratio == 0.8
    assert cfg.window_type == "anchored"


def test_monte_carlo_config_defaults():
    cfg = MonteCarloConfig()
    assert cfg.num_simulations == 1000
    assert cfg.sample_with_replacement is True
    assert cfg.confidence_levels == [5.0, 50.0, 95.0]
    assert cfg.random_seed == 42


def test_monte_carlo_config_custom():
    cfg = MonteCarloConfig(
        num_simulations=500, confidence_levels=[10.0, 50.0, 90.0], random_seed=7
    )
    assert cfg.num_simulations == 500
    assert cfg.confidence_levels == [10.0, 50.0, 90.0]
    assert cfg.random_seed == 7
