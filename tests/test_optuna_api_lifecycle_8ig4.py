"""Tests for Optuna API task lifecycle integration and multiprocess execution (ema_vwap-8ig.4)."""

import time

from fastapi.testclient import TestClient

from src.app import app
from src.config import OptunaConfig, StrategyParams
from src.data_loader import DataLoader
from src.database import CandleDatabase
from src.optimizer import OptunaOptimizer
from src.tasks import TaskManager, TaskStatus

client = TestClient(app)


def test_api_optimize_default_is_sequential():
    """Verify that /api/optimize defaults to sequential execution when execution_mode is omitted."""
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 100,
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "n_trials": 3,
        "sampler_seed": 42,
    }
    response = client.post("/api/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "task_id" in data


def test_api_optimize_multiprocess_explicit_opt_in():
    """Verify that /api/optimize runs in multiprocess mode when explicitly requested."""
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 100,
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "n_trials": 4,
        "execution_mode": "multiprocess",
        "n_workers": 2,
        "sampler_seed": 42,
    }
    response = client.post("/api/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "task_id" in data


def test_api_optimize_worker_clamping():
    """Verify that excessive worker count is clamped to MAX_OPTUNA_WORKERS."""
    payload = {
        "exchange": "synthetic",
        "symbol": "BTC/USD",
        "timeframe": "5m",
        "limit": 100,
        "strategy_mode": "crossover",
        "target_metric": "sharpe_ratio",
        "n_trials": 4,
        "execution_mode": "multiprocess",
        "n_workers": 999,
        "sampler_seed": 42,
    }
    response = client.post("/api/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "task_id" in data


def test_task_manager_multiprocess_progress_and_completion(tmp_path):
    """Verify that a background multiprocess task updates progress and completes cleanly."""
    db = CandleDatabase(db_path=str(tmp_path / "test_system_tasks.db"))
    tm = TaskManager(db=db)

    df_candles = DataLoader.generate_synthetic_candles(num_bars=100, seed=42)
    base_params = StrategyParams(strategy_mode="crossover")
    config = OptunaConfig(
        n_trials=6,
        execution_mode="multiprocess",
        n_workers=2,
        sampler_seed=42,
    )

    def _worker(task_id: str, manager: TaskManager):
        optimizer = OptunaOptimizer(base_params=base_params, config=config)
        res = optimizer.optimize(
            df_candles,
            is_cancelled=lambda: manager.is_cancelled(task_id),
            progress_callback=lambda p, desc: manager.update_progress(task_id, p, desc),
        )
        return {"status": "success", **res}

    task_id = tm.run_in_background("multiprocess_test", _worker)

    # Wait for completion with 10s deadline
    deadline = time.time() + 10.0
    task = None
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task["status"] in (
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
        ):
            break
        time.sleep(0.1)

    assert task is not None
    assert task["status"] == TaskStatus.COMPLETED.value
    assert task["progress_pct"] == 100.0
    assert task["result"]["parallelism_mode"] == "multiprocess"


def test_task_manager_multiprocess_cancellation_bounded(tmp_path):
    """Verify that cancelling a multiprocess background task stops workers within bounded time (<5s)."""
    db = CandleDatabase(db_path=str(tmp_path / "test_system_cancel.db"))
    tm = TaskManager(db=db)

    df_candles = DataLoader.generate_synthetic_candles(num_bars=200, seed=42)
    base_params = StrategyParams(strategy_mode="crossover")
    # Budget of 100 trials across 2 workers so it runs long enough to test cancellation
    config = OptunaConfig(
        n_trials=100,
        execution_mode="multiprocess",
        n_workers=2,
        sampler_seed=42,
    )

    def _worker(task_id: str, manager: TaskManager):
        optimizer = OptunaOptimizer(base_params=base_params, config=config)
        res = optimizer.optimize(
            df_candles,
            is_cancelled=lambda: manager.is_cancelled(task_id),
            progress_callback=lambda p, desc: manager.update_progress(task_id, p, desc),
        )
        return {"status": "success", **res}

    task_id = tm.run_in_background("multiprocess_cancel_test", _worker)

    # Allow task to start
    time.sleep(0.3)
    start_cancel = time.time()
    cancelled = tm.cancel_task(task_id)
    assert cancelled is True

    # Bounded wait: must stop within 5.0 seconds
    deadline = start_cancel + 5.0
    task = None
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task["status"] == TaskStatus.CANCELLED.value:
            break
        time.sleep(0.1)

    cancel_duration = time.time() - start_cancel
    assert cancel_duration < 5.0
    assert task is not None
    assert task["status"] == TaskStatus.CANCELLED.value

    t = tm._active_threads.get(task_id)
    if t is not None:
        t.join(timeout=5.0)
        assert not t.is_alive()


def test_concurrent_multiprocess_tasks_isolated(tmp_path):
    """Verify that concurrent multiprocess optimization tasks execute in isolation."""
    db = CandleDatabase(db_path=str(tmp_path / "test_system_concurrent.db"))
    tm = TaskManager(db=db)

    df1 = DataLoader.generate_synthetic_candles(num_bars=100, seed=1)
    df2 = DataLoader.generate_synthetic_candles(num_bars=100, seed=2)
    params = StrategyParams()

    cfg1 = OptunaConfig(
        n_trials=4, execution_mode="multiprocess", n_workers=2, sampler_seed=11
    )
    cfg2 = OptunaConfig(
        n_trials=4, execution_mode="multiprocess", n_workers=2, sampler_seed=22
    )

    def _make_worker(df, cfg):
        def _w(task_id: str, manager: TaskManager):
            opt = OptunaOptimizer(base_params=params, config=cfg)
            return opt.optimize(df)

        return _w

    t1 = tm.run_in_background("task1", _make_worker(df1, cfg1))
    t2 = tm.run_in_background("task2", _make_worker(df2, cfg2))

    deadline = time.time() + 15.0
    while time.time() < deadline:
        st1 = tm.get_task(t1)
        st2 = tm.get_task(t2)
        if (
            st1
            and st2
            and st1["status"] == TaskStatus.COMPLETED.value
            and st2["status"] == TaskStatus.COMPLETED.value
        ):
            break
        time.sleep(0.2)

    th1 = tm._active_threads.get(t1)
    if th1 is not None:
        th1.join(timeout=2.0)
    th2 = tm._active_threads.get(t2)
    if th2 is not None:
        th2.join(timeout=2.0)

    task1 = tm.get_task(t1)
    task2 = tm.get_task(t2)
    assert task1["status"] == TaskStatus.COMPLETED.value
    assert task2["status"] == TaskStatus.COMPLETED.value
    assert task1["result"]["parallelism_mode"] == "multiprocess"
    assert task2["result"]["parallelism_mode"] == "multiprocess"
