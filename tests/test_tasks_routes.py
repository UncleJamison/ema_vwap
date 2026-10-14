"""
Tests for Task Polling Routes (src/routes/tasks.py):
- GET /api/tasks - list tasks with limit/status filters
- GET /api/tasks/{task_id} - get specific task status
- POST /api/tasks/{task_id}/cancel - cancel task (requires auth)
- TaskManager functionality (create, update, complete, fail, cancel, list)
"""

import threading
import time
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.routes.tasks import router as tasks_router
from src.tasks import TaskCancelledException, TaskManager, TaskStatus


class TestTaskRoutesList:
    """Tests for GET /api/tasks endpoint."""

    def test_list_tasks_empty(self, client, mock_task_db):
        """Should return empty list when no tasks exist."""
        mock_task_db.list_background_tasks = Mock(return_value=[])

        response = client.get("/api/tasks")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["count"] == 0
        assert data["tasks"] == []

    def test_list_tasks_with_data(self, client, mock_task_db):
        """Should return list of tasks."""
        mock_tasks = [
            {
                "task_id": "task_abc123",
                "name": "test_task",
                "status": "completed",
                "progress_pct": 100.0,
                "step_description": "Done",
                "start_time": "2024-01-01T00:00:00",
                "end_time": "2024-01-01T00:01:00",
                "error": None,
                "result": {"data": "test"},
                "created_at": "2024-01-01T00:00:00",
            }
        ]
        mock_task_db.list_background_tasks = Mock(return_value=mock_tasks)

        response = client.get("/api/tasks")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["count"] == 1
        assert data["tasks"][0]["task_id"] == "task_abc123"

    def test_list_tasks_with_limit(self, client, mock_task_db):
        """Should respect limit parameter."""
        mock_task_db.list_background_tasks = Mock(return_value=[])

        response = client.get("/api/tasks?limit=10")

        assert response.status_code == 200
        mock_task_db.list_background_tasks.assert_called_once_with(
            limit=10, status=None
        )

    def test_list_tasks_with_status_filter(self, client, mock_task_db):
        """Should filter by status parameter."""
        mock_task_db.list_background_tasks = Mock(return_value=[])

        response = client.get("/api/tasks?status=running")

        assert response.status_code == 200
        mock_task_db.list_background_tasks.assert_called_once_with(
            limit=50, status="running"
        )

    def test_list_tasks_with_both_params(self, client, mock_task_db):
        """Should respect both limit and status parameters."""
        mock_task_db.list_background_tasks = Mock(return_value=[])

        response = client.get("/api/tasks?limit=25&status=completed")

        assert response.status_code == 200
        mock_task_db.list_background_tasks.assert_called_once_with(
            limit=25, status="completed"
        )

    def test_list_tasks_limit_validation_min(self, client):
        """Should reject limit < 1."""
        response = client.get("/api/tasks?limit=0")
        assert response.status_code == 422

    def test_list_tasks_limit_validation_max(self, client):
        """Should reject limit > 200."""
        response = client.get("/api/tasks?limit=201")
        assert response.status_code == 422


class TestTaskRoutesGet:
    """Tests for GET /api/tasks/{task_id} endpoint."""

    def test_get_task_found(self, client, mock_task_db):
        """Should return task when found."""
        mock_task = {
            "task_id": "task_abc123",
            "name": "test_task",
            "status": "running",
            "progress_pct": 50.0,
            "step_description": "Processing...",
            "start_time": "2024-01-01T00:00:00",
            "end_time": None,
            "error": None,
            "result": None,
            "created_at": "2024-01-01T00:00:00",
        }
        mock_task_db.get_background_task = Mock(return_value=mock_task)

        response = client.get("/api/tasks/task_abc123")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["task"]["task_id"] == "task_abc123"
        assert data["task"]["progress_pct"] == 50.0

    def test_get_task_not_found(self, client, mock_task_db):
        """Should return 404 when task not found."""
        mock_task_db.get_background_task = Mock(return_value=None)

        response = client.get("/api/tasks/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestTaskRoutesCancel:
    """Tests for POST /api/tasks/{task_id}/cancel endpoint."""

    def test_cancel_task_success(self, client, mock_task_db, auth_headers):
        """Should cancel pending/running task with valid auth."""
        mock_task = {
            "task_id": "task_abc123",
            "name": "test_task",
            "status": "running",
            "progress_pct": 50.0,
            "step_description": "Processing...",
            "start_time": "2024-01-01T00:00:00",
            "end_time": None,
            "error": None,
            "result": None,
            "created_at": "2024-01-01T00:00:00",
        }
        mock_task_db.get_background_task = Mock(return_value=mock_task)
        mock_task_db.save_background_task = Mock()

        response = client.post("/api/tasks/task_abc123/cancel", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["task_id"] == "task_abc123"
        assert "cancelled" in data["message"].lower()

    def test_cancel_task_already_completed(self, client, mock_task_db, auth_headers):
        """Should return 400 for already completed task."""
        mock_task = {
            "task_id": "task_abc123",
            "name": "test_task",
            "status": "completed",
            "progress_pct": 100.0,
            "step_description": "Done",
            "start_time": "2024-01-01T00:00:00",
            "end_time": "2024-01-01T00:01:00",
            "error": None,
            "result": {"data": "test"},
            "created_at": "2024-01-01T00:00:00",
        }
        mock_task_db.get_background_task = Mock(return_value=mock_task)

        response = client.post("/api/tasks/task_abc123/cancel", headers=auth_headers)

        assert response.status_code == 400
        assert "could not be cancelled" in response.json()["detail"].lower()

    def test_cancel_task_already_failed(self, client, mock_task_db, auth_headers):
        """Should return 400 for already failed task."""
        mock_task = {
            "task_id": "task_abc123",
            "name": "test_task",
            "status": "failed",
            "progress_pct": 50.0,
            "step_description": "Error",
            "start_time": "2024-01-01T00:00:00",
            "end_time": "2024-01-01T00:01:00",
            "error": "Some error",
            "result": None,
            "created_at": "2024-01-01T00:00:00",
        }
        mock_task_db.get_background_task = Mock(return_value=mock_task)

        response = client.post("/api/tasks/task_abc123/cancel", headers=auth_headers)

        assert response.status_code == 400

    def test_cancel_task_not_found(self, client, mock_task_db, auth_headers):
        """Should return 400 for non-existent task."""
        mock_task_db.get_background_task = Mock(return_value=None)

        response = client.post("/api/tasks/nonexistent/cancel", headers=auth_headers)

        assert response.status_code == 400
        assert "could not be cancelled" in response.json()["detail"].lower()

    def test_cancel_task_requires_auth(self):
        """Should require API key authentication (returns 403 for missing/invalid)."""
        from fastapi import FastAPI

        from src.routes.tasks import router as tasks_router

        # Create app WITHOUT auth override
        app = FastAPI()
        app.include_router(tasks_router)
        # Note: no dependency override for get_api_key
        raw_client = TestClient(app)

        response = raw_client.post("/api/tasks/task_abc123/cancel")
        assert response.status_code == 403


class TestTaskManagerCore:
    """Tests for TaskManager core functionality."""

    @pytest.fixture
    def task_manager(self, tmp_path):
        """Create TaskManager with temporary database."""
        db_path = tmp_path / "test_tasks.db"
        from src.database import CandleDatabase

        db = CandleDatabase(db_path=str(db_path))
        return TaskManager(db=db)

    def test_create_task(self, task_manager):
        """Should create new task and return task_id."""
        task_id = task_manager.create_task("test_task")

        assert task_id.startswith("task_")
        assert len(task_id) > 5

        # Verify task was saved
        task = task_manager.get_task(task_id)
        assert task is not None
        assert task["name"] == "test_task"
        assert task["status"] == TaskStatus.PENDING.value
        assert task["progress_pct"] == 0.0

    def test_update_progress(self, task_manager):
        """Should update task progress."""
        task_id = task_manager.create_task("test_task")

        task_manager.update_progress(task_id, 50.0, "Halfway done")

        task = task_manager.get_task(task_id)
        assert task["status"] == TaskStatus.RUNNING.value
        assert task["progress_pct"] == 50.0
        assert task["step_description"] == "Halfway done"

    def test_update_progress_clamped(self, task_manager):
        """Should clamp progress to 0-100 range."""
        task_id = task_manager.create_task("test_task")

        task_manager.update_progress(task_id, 150.0)
        task = task_manager.get_task(task_id)
        assert task["progress_pct"] == 100.0

        task_manager.update_progress(task_id, -10.0)
        task = task_manager.get_task(task_id)
        assert task["progress_pct"] == 0.0

    def test_update_progress_cancelled_raises(self, task_manager):
        """Should raise TaskCancelledException for cancelled task."""
        task_id = task_manager.create_task("test_task")
        task_manager.cancel_task(task_id)

        with pytest.raises(TaskCancelledException):
            task_manager.update_progress(task_id, 50.0)

    def test_complete_task(self, task_manager):
        """Should mark task as completed with result."""
        task_id = task_manager.create_task("test_task")
        task_manager.update_progress(task_id, 50.0)

        task_manager.complete_task(task_id, {"result": "success"})

        task = task_manager.get_task(task_id)
        assert task["status"] == TaskStatus.COMPLETED.value
        assert task["progress_pct"] == 100.0
        assert task["result"] == {"result": "success"}
        assert task["end_time"] is not None

    def test_fail_task(self, task_manager):
        """Should mark task as failed with error."""
        task_id = task_manager.create_task("test_task")

        task_manager.fail_task(task_id, "Something went wrong")

        task = task_manager.get_task(task_id)
        assert task["status"] == TaskStatus.FAILED.value
        assert task["error"] == "Something went wrong"
        assert "Failed:" in task["step_description"]
        assert task["end_time"] is not None

    def test_cancel_task(self, task_manager):
        """Should cancel pending/running task."""
        task_id = task_manager.create_task("test_task")
        task_manager.update_progress(task_id, 50.0)

        result = task_manager.cancel_task(task_id)

        assert result is True
        task = task_manager.get_task(task_id)
        assert task["status"] == TaskStatus.CANCELLED.value
        assert task["end_time"] is not None

    def test_cancel_task_already_completed_returns_false(self, task_manager):
        """Should return False for already completed task."""
        task_id = task_manager.create_task("test_task")
        task_manager.complete_task(task_id)

        result = task_manager.cancel_task(task_id)

        assert result is False

    def test_is_cancelled(self, task_manager):
        """Should detect cancelled tasks."""
        task_id = task_manager.create_task("test_task")

        assert task_manager.is_cancelled(task_id) is False

        task_manager.cancel_task(task_id)

        assert task_manager.is_cancelled(task_id) is True

    def test_get_task_nonexistent(self, task_manager):
        """Should return None for non-existent task."""
        task = task_manager.get_task("nonexistent")
        assert task is None

    def test_list_tasks(self, task_manager):
        """Should list tasks with limit and status filter."""
        task1 = task_manager.create_task("task1")
        task_manager.complete_task(task1)

        task2 = task_manager.create_task("task2")
        task_manager.fail_task(task2, "error")

        # List all
        tasks = task_manager.list_tasks(limit=50)
        assert len(tasks) == 2

        # List completed only
        tasks = task_manager.list_tasks(limit=50, status="completed")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == task1

        # List failed only
        tasks = task_manager.list_tasks(limit=50, status="failed")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == task2

    def test_run_in_background(self, task_manager):
        """Should execute function in background thread."""
        results = []

        def worker(task_id, manager):
            time.sleep(0.05)
            results.append("done")
            return {"data": "result"}

        task_id = task_manager.run_in_background("bg_task", worker)

        # Wait for completion
        time.sleep(0.2)

        task = task_manager.get_task(task_id)
        assert task["status"] == TaskStatus.COMPLETED.value
        assert task["result"] == {"data": "result"}
        assert results == ["done"]

    def test_run_in_background_cancellation(self, task_manager):
        """Should respect cancellation during execution."""
        _ = threading.Event()

        def worker(task_id, manager):
            time.sleep(0.05)
            if manager.is_cancelled(task_id):
                raise TaskCancelledException("Cancelled")
            return {"data": "result"}

        task_id = task_manager.run_in_background("bg_task", worker)
        time.sleep(0.02)  # Let it start
        task_manager.cancel_task(task_id)
        time.sleep(0.1)

        task = task_manager.get_task(task_id)
        assert task["status"] == TaskStatus.CANCELLED.value

    def test_multiple_tasks_isolation(self, task_manager):
        """Should track multiple tasks independently."""
        task1 = task_manager.create_task("task1")
        task2 = task_manager.create_task("task2")

        task_manager.update_progress(task1, 50.0)
        task_manager.complete_task(task2)

        t1 = task_manager.get_task(task1)
        t2 = task_manager.get_task(task2)

        assert t1["status"] == TaskStatus.RUNNING.value
        assert t1["progress_pct"] == 50.0
        assert t2["status"] == TaskStatus.COMPLETED.value


@pytest.fixture
def task_manager(tmp_path):
    """Create TaskManager with temporary database."""
    db_path = tmp_path / "test_tasks.db"
    from src.database import CandleDatabase

    db = CandleDatabase(db_path=str(db_path))
    return TaskManager(db=db)


class TestTaskManagerThreadSafety:
    """Tests for TaskManager thread safety."""

    def test_concurrent_updates(self, task_manager):
        """Should handle concurrent progress updates safely."""
        task_id = task_manager.create_task("test_task")
        num_threads = 10
        updates_per_thread = 10

        def update_worker():
            for i in range(updates_per_thread):
                task_manager.update_progress(task_id, float(i * 10))
                time.sleep(0.001)

        threads = [threading.Thread(target=update_worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        task = task_manager.get_task(task_id)
        # Should be in valid range
        assert 0.0 <= task["progress_pct"] <= 100.0


class TestTaskRoutesIntegration:
    """Integration tests for tasks routes with real FastAPI app."""

    def test_full_task_lifecycle(self, client, mock_task_db, auth_headers):
        """Test complete task lifecycle through API."""
        # Create a task via TaskManager

        task_id = "task_integration_test"
        mock_task = {
            "task_id": task_id,
            "name": "integration_test",
            "status": "pending",
            "progress_pct": 0.0,
            "step_description": "Task created",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "error": None,
            "result": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Mock the database to return our task
        mock_task_db.get_background_task = Mock(return_value=mock_task)
        mock_task_db.list_background_tasks = Mock(return_value=[mock_task])

        # List tasks
        response = client.get("/api/tasks")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        # Get task
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["task"]["task_id"] == task_id

        # Cancel task (requires auth)
        mock_task_db.save_background_task = Mock()
        response = client.post(f"/api/tasks/{task_id}/cancel", headers=auth_headers)
        assert response.status_code == 200


# Fixtures for test client and mocking
@pytest.fixture
def client():
    """Create test client with tasks router."""
    from fastapi import FastAPI

    from src.auth.security import get_api_key

    app = FastAPI()
    app.include_router(tasks_router)

    # Override auth dependency for testing
    async def override_get_api_key():
        return "test-api-key"

    app.dependency_overrides[get_api_key] = override_get_api_key

    return TestClient(app)


@pytest.fixture
def mock_task_db():
    """Mock the database used by global_task_manager."""
    mock_db = Mock()
    # Patch the global_task_manager's db
    with patch("src.routes.tasks.global_task_manager.db", mock_db), patch(
        "src.tasks.global_task_manager.db", mock_db
    ):
        yield mock_db


@pytest.fixture
def auth_headers():
    """Valid auth headers for protected endpoints."""
    return {"X-API-Key": "test-api-key"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
