"""Tests for Optuna worker contract picklability and parity (ema_vwap-8ig.2)."""

import math
import pickle

import optuna
import pandas as pd

from src.config import OptunaConfig, StrategyParams
from src.data_loader import DataLoader
from src.optimizer import (
    OptunaObjectiveEvaluator,
    OptunaOptimizer,
    OptunaTrialSpec,
    run_worker_trials,
)


def _generate_synthetic_df(n_bars: int = 200, seed: int = 42) -> pd.DataFrame:
    return DataLoader.generate_synthetic_candles(num_bars=n_bars, seed=seed)


def test_spec_and_evaluator_are_picklable():
    """Verify that OptunaTrialSpec, OptunaObjectiveEvaluator, and run_worker_trials can be pickled for Windows spawn."""
    base_params = StrategyParams()
    config = OptunaConfig(n_trials=5, sampler_seed=42)
    spec = OptunaTrialSpec.create(base_params, config)

    # OptunaTrialSpec pickling
    pickled_spec = pickle.dumps(spec)
    unpickled_spec = pickle.loads(pickled_spec)
    assert unpickled_spec.config.n_trials == 5
    assert unpickled_spec.is_auto_mode == spec.is_auto_mode
    assert unpickled_spec.fast_ema_max == spec.fast_ema_max

    # OptunaObjectiveEvaluator pickling
    df = _generate_synthetic_df(100)
    evaluator = OptunaObjectiveEvaluator(spec=spec, df=df)
    pickled_evaluator = pickle.dumps(evaluator)
    unpickled_evaluator = pickle.loads(pickled_evaluator)
    assert len(unpickled_evaluator.df) == 100
    assert unpickled_evaluator.spec.config.n_trials == 5

    # run_worker_trials picklability
    pickled_func = pickle.dumps(run_worker_trials)
    unpickled_func = pickle.loads(pickled_func)
    assert unpickled_func is run_worker_trials


def test_evaluator_deterministic_parity_with_sequential():
    """Verify that running OptunaOptimizer produces identical trial sequences and results."""
    df = _generate_synthetic_df(150, seed=123)
    base_params = StrategyParams(strategy_mode="crossover")
    config = OptunaConfig(n_trials=10, sampler_seed=999, n_jobs=1)

    # Run optimizer (which now uses OptunaObjectiveEvaluator internally)
    optimizer = OptunaOptimizer(base_params=base_params, config=config)
    res = optimizer.optimize(df)

    assert res is not None
    assert "best_params" in res
    assert res["reproducible"] is True
    assert res["parallelism_mode"] == "sequential"

    # Run another optimizer with same seed and verify exact bitwise parity of best_params
    optimizer2 = OptunaOptimizer(base_params=base_params, config=config)
    res2 = optimizer2.optimize(df)
    assert res["best_params"] == res2["best_params"]
    assert res["best_value"] == res2["best_value"]


def test_evaluator_drawdown_constraint_pruning():
    """Verify that OptunaObjectiveEvaluator prunes trials violating hard drawdown constraint."""
    df = _generate_synthetic_df(400, seed=42)
    base_params = StrategyParams(volume_filter_enabled=False, vwap_slope_min=0.0)
    # Enforce extreme max drawdown constraint that is guaranteed to fail
    config = OptunaConfig(
        n_trials=5,
        enable_hard_drawdown_constraint=True,
        max_drawdown_constraint_pct=1.0,
        min_trades=0,
        vwap_slope_min=0.0,
        vwap_slope_max=0.0,
    )
    spec = OptunaTrialSpec.create(base_params, config)
    evaluator = OptunaObjectiveEvaluator(spec=spec, df=df)

    study = optuna.create_study(direction="maximize")

    # If any trial trades, it will breach 0.00001% drawdown and be pruned
    study.optimize(evaluator, n_trials=5)
    # Check that at least some trials were pruned or flagged invalid
    pruned_trials = [
        t for t in study.trials if t.state == optuna.trial.TrialState.PRUNED
    ]
    assert len(pruned_trials) > 0
    # At least one pruned trial should have constraint_violated user_attr
    drawdown_pruned = [
        t
        for t in pruned_trials
        if t.user_attrs.get("constraint_violated") == "max_drawdown"
    ]
    assert len(drawdown_pruned) > 0


def test_evaluator_multi_objective_parity_and_sanitization():
    """Verify that multi-objective evaluation handles non-finite metrics and returns valid tuples."""
    df = _generate_synthetic_df(120, seed=456)
    base_params = StrategyParams()
    config = OptunaConfig(
        n_trials=10,
        sampler_seed=321,
        enable_multi_objective=True,
        multi_objective_metrics=["total_return_pct", "max_drawdown_pct"],
        n_jobs=1,
    )
    spec = OptunaTrialSpec.create(base_params, config)
    evaluator = OptunaObjectiveEvaluator(spec=spec, df=df)

    study = optuna.create_study(
        directions=["maximize", "minimize"],
        sampler=optuna.samplers.TPESampler(seed=321),
    )
    study.optimize(evaluator, n_trials=10)

    for trial in study.trials:
        if trial.state == optuna.trial.TrialState.COMPLETE:
            assert trial.values is not None
            assert len(trial.values) == 2
            assert all(math.isfinite(v) for v in trial.values)
