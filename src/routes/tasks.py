"""
Background Task & Progress Polling Endpoints.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.auth import get_api_key
from src.tasks import global_task_manager

router = APIRouter(tags=["Tasks"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


# ---------------------------------------------------------------------------
# Background Task & Progress Polling Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/tasks")
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
) -> dict[str, Any]:
    """List recent background tasks."""
    tasks = global_task_manager.list_tasks(limit=limit, status=status)
    return {"status": "success", "count": len(tasks), "tasks": tasks}


@router.get("/api/tasks/{task_id}")
def get_task_status(task_id: str) -> dict[str, Any]:
    """Poll specific background task progress and status."""
    task = global_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return {"status": "success", "task": task}


@router.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(get_api_key)])
def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a running background task."""
    success = global_task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Task '{task_id}' could not be cancelled (already finished or not found).",
        )
    return {
        "status": "success",
        "task_id": task_id,
        "message": "Task cancelled successfully.",
    }
