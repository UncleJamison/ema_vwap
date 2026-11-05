"""
Alerting API Endpoints (ema_vwap-6ic).
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.auth import get_api_key
from src.pg_database import get_alerts_pg

router = APIRouter(tags=["Alerts"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


# -------------------------------------------------------------------------
# Alerting API Endpoints (ema_vwap-6ic)
# -------------------------------------------------------------------------
class CreateAlertRequest(BaseModel):
    alert_type: str
    message: str
    level: str = "INFO"
    metadata: dict[str, Any] | None = None


@router.post("/api/alerts", dependencies=[Depends(get_api_key)])
def create_alert(req: CreateAlertRequest) -> dict[str, Any]:
    """Manually trigger an alert via the singleton AlertManager (test/ops)."""
    from src.alerting import get_alert_manager

    entry = get_alert_manager().send(
        req.alert_type, req.message, req.level, req.metadata
    )
    return {"status": "success", "alert": entry}


@router.get("/api/alerts", dependencies=[Depends(get_api_key)])
async def list_alerts(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Return recent alert history (most recent first)."""
    alerts = await get_alerts_pg(limit=limit)
    return {"status": "success", "count": len(alerts), "alerts": alerts}


@router.post("/api/alerts/{alert_id}/acknowledge", dependencies=[Depends(get_api_key)])
def acknowledge_alert(alert_id: int) -> dict[str, Any]:
    """Mark an alert as acknowledged (global — one ack clears for everyone)."""
    ok = _st()._system_db.acknowledge_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "success", "message": "Alert acknowledged", "id": alert_id}


@router.delete("/api/alerts/{alert_id}", dependencies=[Depends(get_api_key)])
def delete_alert(alert_id: int) -> dict[str, Any]:
    """Delete an alert by ID."""
    ok = _st()._system_db.delete_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "success", "message": "Alert deleted", "id": alert_id}
