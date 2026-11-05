"""
Unit tests for TaskManager background job worker, progress polling, and task cancellation.
"""

import time

import pytest

from src.tasks import TaskCancelledException, TaskManager, TaskStatus


@pytest.fixture
def clean_task_db(pg_container):
    """Real PG with a clean background_tasks table per test."""
    import asyncio as _asyncio

    from src.sync_bridge import get_main_loop

    async def _cleanup():
        from src.pg_connection_manager import get_pg_manager

        pool = get_pg_manager().get_pool("system")
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE background_tasks")

    _asyncio.run_coroutine_threadsafe(_cleanup(), get_main_loop()).result(timeout=10)
    yield


def test_task_manager_lifecycle(clean_task_db):
    mgr = TaskManager()

    task_id = mgr.create_task("Test Optimization Job")
    assert task_id.startswith("task_")

    task = mgr.get_task(task_id)
    assert task is not None
    assert task["status"] == TaskStatus.PENDING.value
    assert task["progress_pct"] == 0.0

    mgr.update_progress(task_id, 45.0, "Optimizing trial 15/30")
    task_running = mgr.get_task(task_id)
    assert task_running["status"] == TaskStatus.RUNNING.value
    assert task_running["progress_pct"] == 45.0
    assert task_running["step_description"] == "Optimizing trial 15/30"

    mgr.complete_task(task_id, {"best_sharpe": 2.15})
    task_done = mgr.get_task(task_id)
    assert task_done["status"] == TaskStatus.COMPLETED.value
    assert task_done["progress_pct"] == 100.0
    assert task_done["result"]["best_sharpe"] == 2.15

    task_list = mgr.list_tasks(limit=10)
    assert len(task_list) == 1
    assert task_list[0]["task_id"] == task_id


def test_task_cancellation(clean_task_db):
    mgr = TaskManager()

    task_id = mgr.create_task("Long Running Task")
    mgr.update_progress(task_id, 10.0, "Working...")

    assert mgr.cancel_task(task_id)
    assert mgr.is_cancelled(task_id)

    task = mgr.get_task(task_id)
    assert task["status"] == TaskStatus.CANCELLED.value

    # Updating progress on cancelled task raises TaskCancelledException
    try:
        mgr.update_progress(task_id, 20.0, "More work...")
        assert False, "Should have raised TaskCancelledException"
    except TaskCancelledException:
        pass


def test_background_thread_execution(clean_task_db):
    mgr = TaskManager()

    def worker_job(task_id, manager, items_count):
        for i in range(items_count):
            progress = ((i + 1) / items_count) * 100.0
            manager.update_progress(task_id, progress, f"Step {i + 1}/{items_count}")
            time.sleep(0.01)
        return {"processed": items_count}

    task_id = mgr.run_in_background("Background Worker", worker_job, items_count=5)

    for _ in range(50):
        t = mgr.get_task(task_id)
        if t["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
            break
        time.sleep(0.05)

    final_task = mgr.get_task(task_id)
    assert final_task["status"] == TaskStatus.COMPLETED.value
    assert final_task["progress_pct"] == 100.0
    assert final_task["result"]["processed"] == 5
