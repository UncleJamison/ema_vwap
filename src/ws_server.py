"""
WebSocket Server for Real-Time Paper/Live Trading Dashboard Updates.

Provides WebSocket endpoints for:
- Real-time P&L streaming
- Position updates
- Order fill notifications
- Engine status changes
- Risk metric updates
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime and other non-serializable types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        # Handle other common non-serializable types
        if hasattr(obj, "isoformat"):  # date, time, etc.
            return obj.isoformat()
        if hasattr(obj, "__dict__"):  # custom objects
            return obj.__dict__
        return super().default(obj)


logger = logging.getLogger(__name__)


def _engine():
    """Return the app's configured engine (src.app._paper_engine, constructed
    with db + settings_mgr) at call time — the same object the main app's
    route (src/routes/ws.py) serves. Call-time import avoids import cycles
    and keeps monkeypatched tests working."""
    import src.app as m

    return m._paper_engine


class WebSocketManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, api_key: str | None = None):
        # Verify API key before accepting connection
        from src.auth.security import verify_api_key

        if not api_key or not await verify_api_key(api_key):
            await websocket.close(code=4001, reason="Invalid or missing API key")
            logger.warning("[WebSocket] Connection rejected: invalid API key")
            return

        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(
            f"[WebSocket] Client connected. Total: {len(self.active_connections)}"
        )

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(
            f"[WebSocket] Client disconnected. Total: {len(self.active_connections)}"
        )

    async def broadcast(self, message: dict[str, Any]):
        """Broadcast a message to all connected clients concurrently (ema_vwap-uuid).

        Sends fan out with asyncio.gather instead of sequential awaits, so one
        slow client cannot stall broadcasts to the others.
        """
        if not self.active_connections:
            return

        data = json.dumps(message, cls=DateTimeEncoder)
        async with self._lock:
            targets = list(self.active_connections)

        async def _send(ws):
            try:
                await ws.send_text(data)
                return None
            except (RuntimeError, WebSocketDisconnect):  # Connection closed
                return ws

        # Concurrent fan-out; failed sockets collected for removal
        results = await asyncio.gather(
            *(_send(ws) for ws in targets), return_exceptions=True
        )
        disconnected = [
            r for r in results if r is not None and not isinstance(r, BaseException)
        ]

        async with self._lock:
            for ws in disconnected:
                if ws in self.active_connections:
                    self.active_connections.remove(ws)

    async def send_personal(self, websocket: WebSocket, message: dict[str, Any]):
        """Send a message to a specific client."""
        try:
            await websocket.send_text(json.dumps(message, cls=DateTimeEncoder))
        except RuntimeError:  # Connection closed
            await self.disconnect(websocket)


ws_manager = WebSocketManager()


@asynccontextmanager
async def ws_lifespan(app: FastAPI):
    """Background task for periodic dashboard updates."""

    async def periodic_update():
        while True:
            await asyncio.sleep(5)  # 5-second update interval
            try:
                status = await _engine().get_status()
                await ws_manager.broadcast(
                    {
                        "type": "status_update",
                        "data": status,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            except Exception as e:
                logger.error(f"[WebSocket] Periodic update error: {e}")

    task = asyncio.create_task(periodic_update())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_ws_app() -> FastAPI:
    """Create FastAPI app with WebSocket endpoints for real-time dashboard."""
    app = FastAPI(
        title="EMA+VWAP WebSocket Server",
        description="Real-time paper/live trading dashboard WebSocket API",
        version="1.0.0",
        lifespan=ws_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:8080"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "UPDATE", "DELETE"],
        allow_headers=["*"],
    )

    @app.websocket("/ws/dashboard")
    async def dashboard_websocket(websocket: WebSocket):
        """WebSocket endpoint for real-time dashboard updates."""
        await ws_manager.connect(websocket)
        try:
            # Send initial status
            status = await _engine().get_status()
            await ws_manager.send_personal(
                websocket,
                {
                    "type": "init",
                    "data": status,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

            # Keep connection alive, handle incoming messages
            while True:
                msg = await websocket.receive_text()
                try:
                    data = json.loads(msg)
                    msg_type = data.get("type")

                    if msg_type == "ping":
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "pong",
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        )
                    elif msg_type == "subscribe":
                        # Client can subscribe to specific channels
                        channels = data.get("channels", [])
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "subscribed",
                                "channels": channels,
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        )
                    elif msg_type == "get_status":
                        status = await _engine().get_status()
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "status_update",
                                "data": status,
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        )
                    elif msg_type == "get_positions":
                        positions = await _engine().ledger.alist_positions()
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "positions_update",
                                "data": [p.to_dict() for p in positions],
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        )
                    elif msg_type == "step_engine":
                        # Step engine commands require API key authentication
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "error",
                                "data": "step_engine command disabled: WebSocket requires API key authentication via X-API-Key header.",
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        )
                        # Broadcast updated status to all
                        status = await _engine().get_status()
                        await ws_manager.broadcast(
                            {
                                "type": "status_update",
                                "data": status,
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        )

                except RuntimeError as e:  # Connection closed
                    logger.error(f"[WebSocket] Message handling error: {e}")

        except WebSocketDisconnect:
            await ws_manager.disconnect(websocket)
        except Exception as e:  # Unexpected connection errors
            logger.error(f"[WebSocket] Connection error: {e}")
            await ws_manager.disconnect(websocket)

    @app.get("/ws/status")
    def ws_status() -> dict[str, Any]:
        """Get WebSocket server status and connected client count."""
        return {
            "status": "running",
            "connected_clients": len(ws_manager.active_connections),
        }

    return app


# Standalone WebSocket server for running independently
if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    ws_app = create_ws_app()
    uvicorn.run(ws_app, host="0.0.0.0", port=8081)
