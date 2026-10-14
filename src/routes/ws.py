"""
WebSocket Dashboard Endpoint.
Extracted from src/app.py (ema_vwap-tq41). Shared state is read through
`src.app` at call time so monkeypatched tests keep working.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.logger import logger

router = APIRouter(tags=["WebSocket"])


def _st():
    """Return the src.app module (call-time resolution for monkeypatched state)."""
    import src.app as m

    return m


def _ws():
    """Return the live WebSocket manager (module-level in src.ws_server)."""
    import src.ws_server as ws_mod

    return ws_mod.ws_manager


# WebSocket endpoint for real-time dashboard
@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates."""
    # API key auth: clients may pass ?api_key=... or the X-API-Key header
    api_key = websocket.query_params.get("api_key") or websocket.headers.get(
        "X-API-Key"
    )
    await _ws().connect(websocket, api_key=api_key)
    try:
        # Send initial status
        status = _st()._paper_engine.get_status()
        await _ws().send_personal(
            websocket,
            {
                "type": "init",
                "data": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Keep connection alive, handle incoming messages
        while True:
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
                msg_type = data.get("type")

                if msg_type == "ping":
                    await _ws().send_personal(
                        websocket,
                        {
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                elif msg_type == "get_status":
                    status = _st()._paper_engine.get_status()
                    await _ws().send_personal(
                        websocket,
                        {
                            "type": "status_update",
                            "data": status,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                elif msg_type == "get_positions":
                    positions = _st()._paper_engine.ledger.list_positions()
                    await _ws().send_personal(
                        websocket,
                        {
                            "type": "positions_update",
                            "data": [p.to_dict() for p in positions],
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                elif msg_type == "get_alerts":
                    alerts = _st()._system_db.get_alerts(limit=50)
                    await _ws().send_personal(
                        websocket,
                        {
                            "type": "alerts_update",
                            "data": alerts,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                elif msg_type == "step_engine":
                    # Trigger a single engine step
                    results = _st()._paper_engine.step_all_active_profiles()
                    await _ws().send_personal(
                        websocket,
                        {
                            "type": "engine_step_result",
                            "data": results,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    # Broadcast updated status to all
                    status = _st()._paper_engine.get_status()
                    await _ws().broadcast(
                        {
                            "type": "status_update",
                            "data": status,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )

            except Exception as e:
                logger.error(f"[WebSocket] Message handling error: {e}")

    except WebSocketDisconnect:
        await _ws().disconnect(websocket)
    except Exception as e:
        logger.error(f"[WebSocket] Connection error: {e}")
        await _ws().disconnect(websocket)
