"""
Unit tests for Optuna Strategy Hyperparameter Optimizer.
"""

import json
import warnings
from types import SimpleNamespace

import pytest

from src.config import OptunaConfig, StrategyParams
from src.data_loader import DataLoader
from src.indicators import compute_shared_indicators
from src.optimizer import OptunaOptimizer
from src.strategy import EmaVwapStrategy


def test_optuna_optimizer_basic():
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    params = StrategyParams()
    opt_config = OptunaConfig(n_trials=15, target_metric="sharpe_ratio")

    optimizer = OptunaOptimizer(base_params=params, config=opt_config)
    res = optimizer.optimize(df)

    assert "best_value" in res
    assert "best_params" in res
    assert "param_importance" in res
    assert "top_trials" in res
    assert "best_backtest_result" in res
    assert len(res["top_trials"]) <= 15
    assert res["best_params"]["fast_ema"] >= 3  # Optuna range is 3-30
    assert res["reproducible"] is True
    assert res["parallelism_mode"] == opt_config.execution_mode


@pytest.mark.parametrize("strategy_mode", ["crossover", "multi_ema", "pullback"])
def test_shared_indicator_signals_match_full_indicator_calculation(strategy_mode):
    params = StrategyParams(strategy_mode=strategy_mode)
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    full_result = EmaVwapStrategy(params).generate_signals(df)
    shared_indicators = compute_shared_indicators(df, params)
    shared_result = EmaVwapStrategy(params).generate_signals(
        df, precomputed_indicators=shared_indicators
    )

    assert shared_result["signal"].tolist() == full_result["signal"].tolist()
    for column in ("vwap", "vwap_slope", "atr", "vol_sma", "rvol"):
        assert shared_result[column].equals(full_result[column])


def test_optuna_sampler_seed_reproduces_single_job_study_and_isolates_warnings(
    monkeypatch,
):
    def run_backtest(engine, df):
        total_return_pct = float(
            engine.params.risk_per_trade_pct + engine.params.fast_ema / 100
        )
        metrics = {"total_trades": 10, "total_return_pct": total_return_pct}
        return SimpleNamespace(
            total_trades=10,
            total_return_pct=total_return_pct,
            to_dict=lambda: metrics,
        )

    monkeypatch.setattr("src.optimizer.BacktestEngine.run", run_backtest)
    config = OptunaConfig(
        n_trials=8,
        min_trades=1,
        seed_base_params=False,
        target_metric="total_return_pct",
        sampler_seed=1234,
    )
    optimizer = OptunaOptimizer(StrategyParams(), config)
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    warning_filters = list(warnings.filters)

    first = optimizer.optimize(df)
    second = optimizer.optimize(df)

    assert first["reproducible"] is True
    assert first["best_params"] == second["best_params"]
    assert first["top_trials"] == second["top_trials"]
    assert warnings.filters == warning_filters


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


@pytest.mark.parametrize(
    ("metric", "minimize"),
    [("ulcer_index", True), ("total_return_pct", False)],
)
def test_optuna_respects_metric_direction_and_ranking(monkeypatch, metric, minimize):
    def run_backtest(engine, df):
        metric_value = float(engine.params.risk_per_trade_pct)
        metrics = {"total_trades": 10, metric: metric_value}
        return SimpleNamespace(
            total_trades=10,
            **{metric: metric_value},
            to_dict=lambda: metrics,
        )

    monkeypatch.setattr("src.optimizer.BacktestEngine.run", run_backtest)
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    config = OptunaConfig(
        n_trials=8,
        min_trades=1,
        seed_base_params=False,
        target_metric=metric,
        risk_per_trade_min=0.5,
        risk_per_trade_max=3.0,
    )

    result = OptunaOptimizer(StrategyParams(), config).optimize(df)

    trial_values = [trial["value"] for trial in result["top_trials"]]
    assert trial_values == sorted(trial_values, reverse=not minimize)
    assert result["best_value"] == (
        min(trial_values) if minimize else max(trial_values)
    )
    assert result["best_metric_value"] == result["best_value"]


def test_optuna_trial_preserves_base_settings_and_sampled_stop_family(monkeypatch):
    seen_params = []

    def run_backtest(engine, df):
        seen_params.append(engine.params)
        metric_value = float(engine.params.risk_per_trade_pct)
        metrics = {"total_trades": 10, "total_return_pct": metric_value}
        return SimpleNamespace(
            total_trades=10,
            total_return_pct=metric_value,
            to_dict=lambda: metrics,
        )

    monkeypatch.setattr("src.optimizer.BacktestEngine.run", run_backtest)
    base_params = StrategyParams(
        stop_loss_type="vwap",
        atr_multiplier=99.0,
        vwap_stop_offset_pct=99.0,
        asset_type="stock",
        session_mode="extended",
        vwap_anchor="US_EQUITY",
        allow_fractional_shares=False,
        enforce_margin_calls=True,
        max_holding_bars=20,
        position_size=12.0,
        min_margin_equity=2500.0,
        long_buying_power_ratio=3.0,
        short_buying_power_ratio=1.5,
    )
    config = OptunaConfig(
        n_trials=50,
        min_trades=1,
        seed_base_params=True,
        target_metric="total_return_pct",
    )

    OptunaOptimizer(base_params, config).optimize(
        DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    )

    trial_params = seen_params[:-1]
    assert trial_params[0].stop_loss_type == "vwap"
    assert {"vwap", "atr"}.issuperset(
        {params.stop_loss_type for params in trial_params}
    )
    assert "vwap" in {params.stop_loss_type for params in trial_params}
    assert trial_params[0].vwap_stop_offset_pct == config.vwap_stop_offset_max
    # trailing_stop_pct is now optimized; verify it's within config range
    assert (
        config.trailing_stop_min
        <= trial_params[0].trailing_stop_pct
        <= config.trailing_stop_max
    )
    # Verify sampled params stay within config ranges; exact base
    # settings are already validated above (trial_params[0]).
    for params in trial_params:
        assert params.asset_type in ("crypto", "stock")
        if params.stop_loss_type == "vwap":
            assert (
                config.vwap_stop_offset_min
                <= params.vwap_stop_offset_pct
                <= config.vwap_stop_offset_max
            )
        else:
            assert (
                config.atr_multiplier_min
                <= params.atr_multiplier
                <= config.atr_multiplier_max
            )


@pytest.mark.parametrize(
    "metrics",
    [["sharpe_ratio", "unknown_metric"], ["sharpe_ratio", "sharpe_ratio"]],
)
def test_optuna_rejects_invalid_multi_objective_metrics(metrics):
    config = OptunaConfig(
        enable_multi_objective=True,
        multi_objective_metrics=metrics,
    )

    with pytest.raises(ValueError, match="multi-objective metric"):
        OptunaOptimizer(StrategyParams(), config).optimize(
            DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
        )


def test_multi_objective_all_invalid_trials_return_json_safe_no_result(monkeypatch):
    def run_backtest(engine, df):
        metrics = {
            "total_trades": 0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
        }
        return SimpleNamespace(**metrics, to_dict=lambda: metrics)

    monkeypatch.setattr("src.optimizer.BacktestEngine.run", run_backtest)
    config = OptunaConfig(
        n_trials=3,
        seed_base_params=False,
        enable_multi_objective=True,
        multi_objective_metrics=["sharpe_ratio", "max_drawdown_pct"],
    )

    result = OptunaOptimizer(StrategyParams(), config).optimize(
        DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    )

    assert result["best_value_valid"] is False
    assert result["best_value"] is None
    assert result["top_trials"] == []
    assert result["pareto_frontier"] == []
    json.dumps(result, allow_nan=False)


def test_multi_objective_excludes_invalid_trials_from_pareto_output(monkeypatch):
    calls = 0

    def run_backtest(engine, df):
        nonlocal calls
        calls += 1
        if 1 < calls <= 4:
            metrics = {"total_trades": 0}
            return SimpleNamespace(**metrics, to_dict=lambda: metrics)

        return SimpleNamespace(
            total_trades=10,
            sharpe_ratio=1.5,
            max_drawdown_pct=8.0,
            to_dict=lambda: {
                "total_trades": 10,
                "sharpe_ratio": 1.5,
                "max_drawdown_pct": 8.0,
            },
        )

    monkeypatch.setattr("src.optimizer.BacktestEngine.run", run_backtest)
    config = OptunaConfig(
        n_trials=4,
        n_startup_trials=4,
        seed_base_params=True,
        enable_multi_objective=True,
        multi_objective_metrics=["sharpe_ratio", "max_drawdown_pct"],
    )

    result = OptunaOptimizer(StrategyParams(), config).optimize(
        DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    )

    assert result["best_value_valid"] is True
    assert result["num_pareto_trials"] == 1
    assert len(result["top_trials"]) == 1
    assert len(result["pareto_frontier"]) == 1
    json.dumps(result, allow_nan=False)


def test_multi_objective_prunes_nonfinite_metric_values(monkeypatch):
    def run_backtest(engine, df):
        metrics = {
            "total_trades": 10,
            "sharpe_ratio": float("inf"),
            "max_drawdown_pct": 5.0,
        }
        return SimpleNamespace(**metrics, to_dict=lambda: metrics)

    monkeypatch.setattr("src.optimizer.BacktestEngine.run", run_backtest)
    config = OptunaConfig(
        n_trials=2,
        seed_base_params=False,
        enable_multi_objective=True,
        multi_objective_metrics=["sharpe_ratio", "max_drawdown_pct"],
    )

    result = OptunaOptimizer(StrategyParams(), config).optimize(
        DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    )

    assert result["best_value_valid"] is False
    assert result["best_value"] is None
    assert result["top_trials"] == []
    json.dumps(result, allow_nan=False)


def test_batch_optimizer_skips_no_valid_optimization_result(monkeypatch):

    import src.batch_optimizer as batch_module

    df = DataLoader.generate_synthetic_candles(num_bars=600, seed=42)
    observed_config = []
    monkeypatch.setattr(
        batch_module.DataLoader,
        "load_candles_sync",
        lambda **kwargs: df,
    )
    monkeypatch.setattr(
        "src.indicators.compute_slope_bounds_from_df",
        lambda *args, **kwargs: {
            "vwap_slope_min": 0.0001,
            "vwap_slope_max": 0.003,
        },
    )
    monkeypatch.setattr(
        batch_module.OptunaOptimizer,
        "optimize",
        lambda self, df=None, is_cancelled=None, progress_callback=None: observed_config.append(
            (self.config.min_trades, self.config.sampler_seed)
        )
        or {
            "best_params": {},
            "best_value": None,
            "best_value_valid": False,
            "best_backtest_result": None,
        },
    )

    # Mock save_batch_result_pg to fail if called
    async def mock_save_batch_result_pg(*args, **kwargs):
        pytest.fail("invalid optimization result must not be persisted")

    monkeypatch.setattr(
        "src.batch_optimizer.save_batch_result_pg",
        mock_save_batch_result_pg,
    )

    batch_optimizer = batch_module.BatchOptimizer(
        batch_module.BatchOptimizerConfig(
            symbols=["BTC/USD"],
            timeframes=["5m"],
            min_candles=500,
            min_trades=1,
            sampler_seed=91,
            enable_multi_objective=True,
        )
    )

    import asyncio

    asyncio.run(batch_optimizer._run_single_optuna("BTC/USD", "5m"))
    assert observed_config == [(1, 91)]


def test_batch_optimizer_uses_configured_min_trades_for_persistence(monkeypatch):

    import src.batch_optimizer as batch_module

    df = DataLoader.generate_synthetic_candles(num_bars=600, seed=42)
    saved_results = []
    monkeypatch.setattr(
        batch_module.DataLoader,
        "load_candles_sync",
        lambda **kwargs: df,
    )
    monkeypatch.setattr(
        "src.indicators.compute_slope_bounds_from_df",
        lambda *args, **kwargs: {
            "vwap_slope_min": 0.0001,
            "vwap_slope_max": 0.003,
        },
    )
    monkeypatch.setattr(
        batch_module.OptunaOptimizer,
        "optimize",
        lambda self, df, is_cancelled=None, progress_callback=None: {
            "best_params": {"fast_ema": 10, "slow_ema": 20},
            "best_value": 1.0,
            "best_value_valid": True,
            "best_backtest_result": {"total_trades": 2},
        },
    )

    # Mock save_batch_result_pg to capture saved results
    async def mock_save_batch_result_pg(*args, **kwargs):
        saved_results.append(kwargs)

    monkeypatch.setattr(
        "src.batch_optimizer.save_batch_result_pg",
        mock_save_batch_result_pg,
    )

    batch_optimizer = batch_module.BatchOptimizer(
        batch_module.BatchOptimizerConfig(
            symbols=["BTC/USD"],
            timeframes=["5m"],
            min_candles=500,
            min_trades=1,
        )
    )

    import asyncio

    asyncio.run(batch_optimizer._run_single_optuna("BTC/USD", "5m"))

    assert len(saved_results) == 1
    assert saved_results[0]["trade_count"] == 2


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
    assert res["reproducible"] is True
    assert res["parallelism_mode"] == opt_config.execution_mode


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


def test_optuna_optimizer_rejects_infeasible_relational_ema_ranges():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    config = OptunaConfig(
        fast_ema_min=10,
        fast_ema_max=20,
        slow_ema_min=5,
        slow_ema_max=8,
        trend_ema_min=9,
        trend_ema_max=12,
    )

    with pytest.raises(ValueError, match="No feasible fast < slow < trend EMA range"):
        OptunaOptimizer(StrategyParams(strategy_mode="multi_ema"), config).optimize(df)


def test_optuna_relational_delta_ranges_avoid_pruned_ema_trials(monkeypatch):
    def run_backtest(engine, df):
        metrics = {
            "total_trades": 10,
            "total_return_pct": float(
                engine.params.fast_ema
                + engine.params.slow_ema
                + engine.params.trend_ema
            ),
        }
        return SimpleNamespace(**metrics, to_dict=lambda: metrics)

    monkeypatch.setattr("src.optimizer.BacktestEngine.run", run_backtest)
    config = OptunaConfig(
        n_trials=15,
        n_startup_trials=15,
        min_trades=1,
        seed_base_params=False,
        target_metric="total_return_pct",
        fast_ema_min=2,
        fast_ema_max=5,
        slow_ema_min=10,
        slow_ema_max=25,
        trend_ema_min=15,
        trend_ema_max=60,
    )
    bounds = OptunaOptimizer._get_relational_ema_search_bounds(config)
    # Check base (minimum) corner only - this is the primary valid configuration
    fast_ema = config.fast_ema_min
    slow_delta = bounds["slow_delta_min"]
    slow_ema = fast_ema + slow_delta
    trend_delta = bounds["trend_delta_min"]
    trend_ema = slow_ema + trend_delta
    assert config.slow_ema_min <= slow_ema <= config.slow_ema_max
    assert config.trend_ema_min <= trend_ema <= config.trend_ema_max
    assert fast_ema < slow_ema < trend_ema

    result = OptunaOptimizer(StrategyParams(), config).optimize(
        DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    )

    assert len(result["top_trials"]) == config.n_trials


def test_optuna_optimizer_requires_valid_trial():
    df = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    config = OptunaConfig(n_trials=2, min_trades=10_000)

    result = OptunaOptimizer(StrategyParams(), config).optimize(df)

    assert result["best_value_valid"] is False
    assert result["best_value"] is None


def test_optuna_optimizer_with_df_calibrated_slope_bounds():
    from src.indicators import compute_slope_bounds_from_df

    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    slope_bounds = compute_slope_bounds_from_df(df, timeframe="5m", min_samples=50)

    assert slope_bounds["vwap_slope_min"] < slope_bounds["vwap_slope_max"]

    config = OptunaConfig(
        n_trials=4,
        target_metric="sharpe_ratio",
        vwap_slope_min=slope_bounds["vwap_slope_min"],
        vwap_slope_max=slope_bounds["vwap_slope_max"],
    )
    optimizer = OptunaOptimizer(base_params=StrategyParams(), config=config)
    res = optimizer.optimize(df)

    assert "best_params" in res
    assert "top_trials" in res
    for trial in res["top_trials"]:
        if "vwap_slope_threshold" in trial["params"]:
            assert (
                slope_bounds["vwap_slope_min"]
                <= trial["params"]["vwap_slope_threshold"]
                <= slope_bounds["vwap_slope_max"]
            )
