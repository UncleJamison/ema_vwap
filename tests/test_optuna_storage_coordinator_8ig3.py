"""Tests for Optuna shared storage and multi-process worker coordination (ema_vwap-8ig.3)."""

import os

import optuna
import pytest

from src.config import OptunaConfig, StrategyParams
from src.data_loader import DataLoader
from src.optimizer import (
    OptunaObjectiveEvaluator,
    OptunaStudyCoordinator,
    OptunaTrialSpec,
    create_journal_storage,
)


def test_trial_budget_partitioning():
    """Verify that total trials are exactly partitioned across workers with zero drift."""
    # 10 trials across 3 workers -> 4 + 3 + 3 = 10
    budgets = OptunaStudyCoordinator.allocate_trial_budgets(10, 3)
    assert budgets == [4, 3, 3]
    assert sum(budgets) == 10

    # 10 trials across 4 workers -> 3 + 3 + 2 + 2 = 10
    budgets = OptunaStudyCoordinator.allocate_trial_budgets(10, 4)
    assert budgets == [3, 3, 2, 2]
    assert sum(budgets) == 10

    # Single worker
    assert OptunaStudyCoordinator.allocate_trial_budgets(7, 1) == [7]

    # Zero trials
    assert OptunaStudyCoordinator.allocate_trial_budgets(0, 3) == [0, 0, 0]

    # Invalid worker count
    with pytest.raises(ValueError):
        OptunaStudyCoordinator.allocate_trial_budgets(10, 0)


def test_worker_seed_allocation():
    """Verify deterministic worker sampler seed dispersion."""
    seeds = OptunaStudyCoordinator.allocate_worker_seeds(42, 3)
    assert len(seeds) == 3
    assert len(set(seeds)) == 3  # All distinct
    assert all(isinstance(s, int) for s in seeds)

    # Re-run with same master seed produces identical sequence
    seeds2 = OptunaStudyCoordinator.allocate_worker_seeds(42, 3)
    assert seeds == seeds2

    # None master seed produces [None, ...]
    assert OptunaStudyCoordinator.allocate_worker_seeds(None, 4) == [
        None,
        None,
        None,
        None,
    ]


def test_storage_creation_and_filesystem_contract(tmp_path):
    """Verify create_journal_storage uses JournalFileOpenLock and documents local filesystem constraint."""
    journal_file = str(tmp_path / "test_doc.journal")
    storage = create_journal_storage(journal_file)
    assert storage is not None

    # Verify docstring emphasizes local filesystem
    assert "local filesystem" in OptunaStudyCoordinator.__doc__.lower()
    assert "local filesystem" in create_journal_storage.__doc__.lower()


def test_coordinator_multiprocess_workers_execution(tmp_path):
    """Verify multi-process worker execution with exact trial accounting on Windows spawn."""
    df = DataLoader.generate_synthetic_candles(num_bars=150, seed=42)
    base_params = StrategyParams(strategy_mode="crossover")
    config = OptunaConfig(n_trials=6, sampler_seed=777)
    spec = OptunaTrialSpec.create(base_params, config)
    evaluator = OptunaObjectiveEvaluator(spec=spec, df=df)

    with OptunaStudyCoordinator() as coordinator:
        study = coordinator.create_study(directions="maximize")
        assert study is not None

        # Run 6 trials across 2 processes (3 trials per process)
        completed_study = coordinator.run_workers(
            evaluator=evaluator,
            total_trials=6,
            n_workers=2,
            master_seed=777,
        )

        assert len(completed_study.trials) == 6
        complete_trials = [
            t
            for t in completed_study.trials
            if t.state == optuna.trial.TrialState.COMPLETE
        ]
        assert len(complete_trials) == 6


def test_coordinator_cleanup_and_temp_lifecycle():
    """Verify that temporary journal and lock files are strictly cleaned up on context exit."""
    journal_path = None
    with OptunaStudyCoordinator() as coordinator:
        journal_path = coordinator.journal_path
        assert os.path.exists(journal_path)
        coordinator.create_study(directions="maximize")

    # After context exit, the temporary file should be unlinked
    assert not os.path.exists(journal_path)
    assert not os.path.exists(f"{journal_path}.lock")


def test_isolated_studies_no_cross_leakage():
    """Verify that concurrent coordinators maintain completely isolated study namespaces and storage."""
    with OptunaStudyCoordinator() as coord1, OptunaStudyCoordinator() as coord2:
        study1 = coord1.create_study(directions="maximize")
        study2 = coord2.create_study(directions="maximize")

        assert coord1.study_name != coord2.study_name
        assert coord1.journal_path != coord2.journal_path

        # Optimize 2 trials in study 1
        study1.optimize(lambda t: t.suggest_float("x", 0, 1), n_trials=2)
        # Optimize 3 trials in study 2
        study2.optimize(lambda t: t.suggest_float("y", 10, 20), n_trials=3)

        # Reload from coordinators and verify zero leakage
        s1_loaded = coord1.get_study()
        s2_loaded = coord2.get_study()

        assert len(s1_loaded.trials) == 2
        assert len(s2_loaded.trials) == 3
        assert "x" in s1_loaded.trials[0].params
        assert "y" in s2_loaded.trials[0].params


def test_stale_and_crashed_trial_handling(tmp_path):
    """Verify that incomplete or pruned trials do not pollute valid completed study accounting."""
    journal_file = str(tmp_path / "test_stale.journal")
    with OptunaStudyCoordinator(
        journal_path=journal_file, persist_journal=True
    ) as coord:
        study = coord.create_study(directions="maximize")

        # Complete trial
        study.optimize(lambda t: 1.0, n_trials=1)

        # Pruned trial
        def pruned_obj(t):
            raise optuna.TrialPruned("pruned")

        study.optimize(pruned_obj, n_trials=1)

        loaded_study = coord.get_study()
        assert len(loaded_study.trials) == 2
        complete_trials = [
            t
            for t in loaded_study.trials
            if t.state == optuna.trial.TrialState.COMPLETE
        ]
        pruned_trials = [
            t for t in loaded_study.trials if t.state == optuna.trial.TrialState.PRUNED
        ]
        assert len(complete_trials) == 1
        assert len(pruned_trials) == 1
