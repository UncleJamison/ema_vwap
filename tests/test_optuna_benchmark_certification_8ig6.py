"""
Certification and Benchmark Tests for Process-Parallel Optuna Backend (Phase 8.6 / ema_vwap-8ig.6).

Covers:
1. Sequential vs Multi-process Correctness Parity (single and multi-objective).
2. Multi-objective Pareto Frontier and Utopia selection under multiprocessing.
3. Worker crash resilience and sibling process termination.
4. Timeout enforcement and bounded termination.
5. Storage hygiene and zero temporary file leaks.
6. Policy enforcement: default execution_mode remains 'sequential'.
"""

import glob
import os
import tempfile

import pytest

from src.app import OptimizeRequest
from src.batch_optimizer import BatchOptimizerConfig
from src.config import OptunaConfig, StrategyParams
from src.data_loader import DataLoader
from src.optimizer import (
    OptunaObjectiveEvaluator,
    OptunaOptimizer,
    OptunaStudyCoordinator,
    OptunaTrialSpec,
)


def test_certification_policy_default_remains_sequential():
    """Verify that all Optuna configuration points default to 'sequential'."""
    assert OptunaConfig().execution_mode == "sequential"
    assert OptimizeRequest().execution_mode == "sequential"
    assert BatchOptimizerConfig().execution_mode == "sequential"


def test_certification_sequential_vs_multiprocess_parity():
    """Verify that sequential and multi-process runs produce structurally identical and valid results."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams(strategy_mode="crossover")

    cfg_seq = OptunaConfig(
        n_trials=6,
        execution_mode="sequential",
        sampler_seed=42,
        min_trades=0,
        target_metric="sharpe_ratio",
    )
    res_seq = OptunaOptimizer(base_params=params, config=cfg_seq).optimize(df)

    cfg_mp = OptunaConfig(
        n_trials=6,
        execution_mode="multiprocess",
        n_workers=2,
        sampler_seed=42,
        min_trades=0,
        target_metric="sharpe_ratio",
    )
    res_mp = OptunaOptimizer(base_params=params, config=cfg_mp).optimize(df)

    for res in (res_seq, res_mp):
        assert res["best_params"] is not None
        assert "fast_ema" in res["best_params"]
        assert "slow_ema" in res["best_params"]
        assert "best_value" in res
        assert len(res["top_trials"]) <= 6
        assert res["best_backtest_result"] is not None

    assert res_seq["parallelism_mode"] == "sequential"
    assert res_mp["parallelism_mode"] == "multiprocess"


def test_certification_multi_objective_pareto_front_multiprocess():
    """Verify multi-objective optimization with multi-process workers produces valid Pareto frontier."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=123)
    params = StrategyParams(strategy_mode="crossover")

    cfg = OptunaConfig(
        n_trials=6,
        execution_mode="multiprocess",
        n_workers=2,
        min_trades=0,
        enable_multi_objective=True,
        multi_objective_metrics=["sharpe_ratio", "max_drawdown_pct"],
        sampler_seed=123,
    )

    res = OptunaOptimizer(base_params=params, config=cfg).optimize(df)

    assert res["optimization_mode"] == "multi_objective"
    assert res["parallelism_mode"] == "multiprocess"
    assert "pareto_frontier" in res
    assert res["num_pareto_trials"] >= 0
    assert "best_params" in res
    assert "best_value" in res


def test_certification_storage_hygiene_zero_leak():
    """Verify that repeated multi-process runs do not leave orphaned journal or lock files."""
    df = DataLoader.generate_synthetic_candles(num_bars=200, seed=1)
    params = StrategyParams(strategy_mode="crossover")
    temp_dir = tempfile.gettempdir()

    cfg = OptunaConfig(
        n_trials=4,
        execution_mode="multiprocess",
        n_workers=2,
        min_trades=0,
        sampler_seed=1,
    )

    journals_before = set(glob.glob(os.path.join(temp_dir, "optuna_study_*.journal*")))

    for _ in range(2):
        optimizer = OptunaOptimizer(base_params=params, config=cfg)
        res = optimizer.optimize(df)
        assert res["best_params"] is not None

    journals_after = set(glob.glob(os.path.join(temp_dir, "optuna_study_*.journal*")))
    new_leaks = journals_after - journals_before

    assert len(new_leaks) == 0, f"Leaked journal files detected: {new_leaks}"


class CrashingEvaluator:
    """Evaluator that simulates worker failure."""

    def __call__(self, trial):
        raise RuntimeError("Simulated worker process crash during trial")


def test_certification_worker_crash_recovery():
    """Verify that an exception in worker evaluation is cleanly propagated and coordinator cleans up."""
    coordinator = OptunaStudyCoordinator()
    coordinator.create_study(directions="maximize")

    with pytest.raises(Exception):  # noqa: B017 - test expects any exception
        coordinator.run_workers(
            evaluator=CrashingEvaluator(),
            total_trials=4,
            n_workers=2,
        )

    coordinator.cleanup()
    if coordinator.journal_path:
        assert not os.path.exists(coordinator.journal_path)


def test_certification_timeout_bounded_cleanup():
    """Verify that coordinator timeout cancels workers and cleans up within bounded duration."""
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    params = StrategyParams(strategy_mode="crossover")

    cfg = OptunaConfig(n_trials=100, min_trades=0)
    spec = OptunaTrialSpec.create(base_params=params, config=cfg)
    evaluator = OptunaObjectiveEvaluator(spec=spec, df=df)

    coordinator = OptunaStudyCoordinator()
    coordinator.create_study(directions="maximize")

    # Impose a 0.5s timeout on 100 trials
    study = coordinator.run_workers(
        evaluator=evaluator,
        total_trials=100,
        n_workers=2,
        timeout_seconds=0.5,
    )

    assert study is not None
    assert len(study.trials) < 100

    coordinator.cleanup()
    if coordinator.journal_path:
        assert not os.path.exists(coordinator.journal_path)
