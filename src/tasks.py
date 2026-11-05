"""
Background Job Worker and Progress Polling Manager.
Executes long-running historical data syncs, Optuna optimizations, Monte Carlo simulations,
and walk-forward analyses in background worker threads with progress tracking and task cancellation.
"""

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.logger import logger
from src.pg_database import (
    get_background_task_pg,
    list_background_tasks_pg,
    save_background_task_pg,
)
from src.sync_bridge import run_async


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCancelledException(Exception):
    """Raised when a background task execution is interrupted by cancellation."""


class TaskManager:
    """Thread-safe background task execution & status polling manager."""

    def __init__(self, db: Any | None = None) -> None:
        """Create a task manager.

        ``db`` is accepted for backward compatibility with callers/tests that
        pass a SQLite CandleDatabase; the manager now resolves PG domains
        directly via pg_database.py functions.
        """
        self._active_threads: dict[str, threading.Thread] = {}
        self._cancelled_ids: set = set()
        self._lock = threading.Lock()

    async def _save_task(self, task_data: dict) -> None:
        """Save task to PostgreSQL."""
        await save_background_task_pg(task_data)

    async def _get_task(self, task_id: str) -> dict | None:
        """Get task from PostgreSQL."""
        return await get_background_task_pg(task_id)

    async def _list_tasks(
        self, limit: int = 50, status: str | None = None
    ) -> list[dict]:
        """List tasks from PostgreSQL."""
        return await list_background_tasks_pg(limit=limit, status=status)

    def create_task(self, name: str) -> str:
        """Create a new background task record and return unique task ID."""
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(UTC).isoformat()
        task_data = {
            "task_id": task_id,
            "name": name,
            "status": TaskStatus.PENDING.value,
            "progress_pct": 0.0,
            "step_description": "Task created",
            "start_time": now_iso,
            "end_time": None,
            "error": None,
            "result": None,
            "created_at": now_iso,
        }
        # Run async save via sync bridge (submits to main loop or runs fresh)
        run_async(self._save_task(task_data))
        logger.info(f"Background task created: {task_id} ('{name}')")
        return task_id

    def update_progress(
        self, task_id: str, progress_pct: float, step_description: str = ""
    ) -> None:
        """Update progress percentage and status description for a running task."""
        if self.is_cancelled(task_id):
            raise TaskCancelledException(f"Task {task_id} was cancelled.")

        task = run_async(get_background_task_pg(task_id))
        if not task:
            return

        task["status"] = TaskStatus.RUNNING.value
        task["progress_pct"] = max(0.0, min(100.0, float(progress_pct)))
        if step_description:
            task["step_description"] = str(step_description)

        run_async(save_background_task_pg(task))

    def complete_task(self, task_id: str, result: dict[str, Any] | None = None) -> None:
        """Mark background task as completed with final result payload."""
        task = run_async(get_background_task_pg(task_id))
        if not task:
            return

        now_iso = datetime.now(UTC).isoformat()
        task["status"] = TaskStatus.COMPLETED.value
        task["progress_pct"] = 100.0
        task["step_description"] = "Completed successfully"
        task["end_time"] = now_iso
        task["result"] = result

        run_async(save_background_task_pg(task))
        logger.info(f"Background task completed: {task_id}")

        with self._lock:
            self._active_threads.pop(task_id, None)

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark background task as failed with error description."""
        task = run_async(get_background_task_pg(task_id))
        if not task:
            return

        now_iso = datetime.now(UTC).isoformat()
        task["status"] = TaskStatus.FAILED.value
        task["end_time"] = now_iso
        task["error"] = str(error)
        task["step_description"] = f"Failed: {error}"

        run_async(save_background_task_pg(task))
        logger.error(f"Background task failed: {task_id} — Error: {error}")

        with self._lock:
            self._active_threads.pop(task_id, None)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or running background task."""
        task = run_async(get_background_task_pg(task_id))
        if not task:
            return False

        if task["status"] in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
            return False

        with self._lock:
            self._cancelled_ids.add(task_id)

        now_iso = datetime.now(UTC).isoformat()
        task["status"] = TaskStatus.CANCELLED.value
        task["end_time"] = now_iso
        task["step_description"] = "Task cancelled by user"
        run_async(save_background_task_pg(task))

        logger.info(f"Background task cancelled: {task_id}")
        return True

    def is_cancelled(self, task_id: str) -> bool:
        """Check if a task has received a cancellation request."""
        with self._lock:
            if task_id in self._cancelled_ids:
                return True
        task = run_async(get_background_task_pg(task_id))
        return bool(task and task.get("status") == TaskStatus.CANCELLED.value)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Fetch status and progress for a single task."""
        return run_async(get_background_task_pg(task_id))

    def list_tasks(
        self, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        """List recent tasks."""
        return run_async(list_background_tasks_pg(limit=limit, status=status))

    def run_in_background(
        self, task_name: str, target_func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> str:
        """
        Create background task and spawn worker thread to execute target_func(task_id, *args, **kwargs).
        Returns task_id string.
        """
        task_id = self.create_task(name=task_name)

        def worker_wrapper():
            try:
                self.update_progress(task_id, 0.0, "Starting background task...")
                result = target_func(task_id, self, *args, **kwargs)
                if not self.is_cancelled(task_id):
                    self.complete_task(
                        task_id,
                        result if isinstance(result, dict) else {"data": result},
                    )
            except TaskCancelledException:
                logger.info(f"Worker caught cancellation for task {task_id}")
            except Exception as e:
                self.fail_task(task_id, str(e))

        thread = threading.Thread(
            target=worker_wrapper, daemon=True, name=f"Worker-{task_id}"
        )
        with self._lock:
            self._active_threads[task_id] = thread
        thread.start()
        return task_id


# Global Singleton TaskManager Instance
global_task_manager = TaskManager()
