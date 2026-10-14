"""
Audit Log Routes (ema_vwap-108).
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.audit.logger import AuditEventType, AuditLogger, get_audit_logger
from src.auth.dependencies import require_audit_read

router = APIRouter(prefix="/api/audit", tags=["Audit"])


class AuditLogResponse(BaseModel):
    event_id: str
    timestamp: str
    event_type: str
    user_id: str | None = None
    username: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None
    action: str
    details: dict[str, Any] = {}
    success: bool
    error_message: str | None = None


class AuditLogListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    logs: list[AuditLogResponse]


@router.get("", dependencies=[Depends(require_audit_read)])
async def list_audit_logs(
    user_id: str | None = None,
    event_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> AuditLogListResponse:
    """Query audit logs with filters."""
    try:
        # Parse datetime filters
        start_time_dt = None
        end_time_dt = None
        if start_time:
            start_time_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if end_time:
            end_time_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

        if event_type:
            try:
                _ = AuditEventType(event_type)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"Invalid event_type: {event_type}"
                )

        logs = audit_logger.query(
            user_id=user_id,
            event_type=AuditEventType(event_type) if event_type else None,
            start_time=start_time_dt,
            end_time=end_time_dt,
            limit=limit,
            offset=offset,
        )

        return AuditLogListResponse(
            total=len(logs),
            limit=limit,
            offset=offset,
            logs=[AuditLogResponse(**log) for log in logs],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to query audit logs: {e!s}"
        )


@router.get("/event-types")
async def list_event_types() -> dict[str, list[str]]:
    """List all available audit event types."""
    return {"event_types": [e.value for e in AuditEventType]}


@router.get("/stats")
async def get_audit_stats(
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    """Get audit log statistics."""
    # This would require additional query methods in the audit logger
    return {
        "message": "Statistics endpoint not yet implemented",
        "suggested_filters": ["start_time", "end_time"],
    }
