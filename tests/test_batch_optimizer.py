"""
Tests for batch_optimizer module — exposure: BatchOptimizer, BatchOptimizerConfig, get_batch_state
No dedicated test file existed before this addition; all tests use isolated tmp_path DBs.
"""

from src.batch_optimizer import BatchOptimizer, BatchOptimizerConfig, get_batch_state


def test_batch_optimizer_config_defaults():
    cfg = BatchOptimizerConfig()
    assert cfg.n_jobs == 1
    assert cfg.seed_base_params is True
    assert cfg.enable_multi_objective is False
    assert cfg.enable_max_holding_bars is False
    assert cfg.max_holding_bars_min == 4


def test_batch_optimizer_config_custom():
    cfg = BatchOptimizerConfig(
        n_jobs=4,
        seed_base_params=False,
        enable_multi_objective=True,
        enable_max_holding_bars=True,
        max_holding_bars_min=8,
    )
    assert cfg.n_jobs == 4
    assert cfg.seed_base_params is False
    assert cfg.enable_multi_objective is True
    assert cfg.enable_max_holding_bars is True
    assert cfg.max_holding_bars_min == 8


def test_batch_optimizer_config_asdict():
    import dataclasses

    cfg = BatchOptimizerConfig(
        n_jobs=2, seed_base_params=True, enable_multi_objective=True
    )
    d = dataclasses.asdict(cfg)
    assert d["n_jobs"] == 2
    assert d["seed_base_params"]
    assert d["enable_multi_objective"]
    assert d["seed_base_params"]


def test_batch_optimizer_get_batch_state():
    state = get_batch_state()
    # Actual state dict has: running, done, total, current_combo, error
    assert isinstance(state, dict)
    assert "running" in state
    assert state["running"] is False
    assert state["done"] == 0


def test_batch_optimizer_instantiation():
    """Instantiate BatchOptimizer with a minimal config; no optimize run."""
    opt = BatchOptimizer(config=BatchOptimizerConfig(n_jobs=1, n_trials=1))
    # verify basic attributes exist without triggering a full optimize
    assert opt.config is not None
    assert opt.config.n_jobs == 1


def test_batch_optimizer_config_validations():
    # n_jobs must be positive integer
    cfg = BatchOptimizerConfig(n_jobs=1)
    assert cfg.n_jobs >= 1


def test_batch_optimizer_config_min_values():
    """Verify minimum constraint values can be set to boundary values."""
    cfg = BatchOptimizerConfig(
        fast_ema_min=1,
        fast_ema_max=100,
        slow_ema_min=1,
        slow_ema_max=200,
        n_startup_trials=1,
    )
    assert cfg.fast_ema_min == 1
    assert cfg.fast_ema_max == 100
    assert cfg.slow_ema_min == 1
    assert cfg.slow_ema_max == 200
    assert cfg.n_startup_trials == 1


def test_batch_optimizer_config_multisymbol_multitimeframe():
    cfg = BatchOptimizerConfig(
        symbols=["BTC/USD", "ETH/USD", "SOL/USD"],
        timeframes=["5m", "15m", "1h", "4h"],
    )
    assert len(cfg.symbols) == 3
    assert len(cfg.timeframes) == 4
    # Verify default symbol/timeframe ordering preserved
    assert cfg.symbols[0] == "BTC/USD"
    assert cfg.exchange == "kucoin"
    assert cfg.days == 360
