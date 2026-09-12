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
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
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
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return

        data = json.dumps(message)
        async with self._lock:
            disconnected = []
            for ws in self.active_connections:
                try:
                    await ws.send_text(data)
                except RuntimeError:  # Connection closed
                    disconnected.append(ws)

            for ws in disconnected:
                if ws in self.active_connections:
                    self.active_connections.remove(ws)

    async def send_personal(self, websocket: WebSocket, message: dict[str, Any]):
        """Send a message to a specific client."""
        try:
            await websocket.send_text(json.dumps(message))
        except RuntimeError:  # Connection closed
            await self.disconnect(websocket)


ws_manager = WebSocketManager()


@asynccontextmanager
async def ws_lifespan(app: FastAPI):
    """Background task for periodic dashboard updates."""
    from src.paper.engine import _paper_engine

    async def periodic_update():
        while True:
            await asyncio.sleep(5)  # 5-second update interval
            try:
                status = _paper_engine.get_status()
                await ws_manager.broadcast(
                    {
                        "type": "status_update",
                        "data": status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as e:  # Broadcast errors are non-critical
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
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.websocket("/ws/dashboard")
    async def dashboard_websocket(websocket: WebSocket):
        """WebSocket endpoint for real-time dashboard updates."""
        await ws_manager.connect(websocket)
        try:
            # Send initial status
            from src.paper.engine import _paper_engine

            status = _paper_engine.get_status()
            await ws_manager.send_personal(
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
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "pong",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    elif msg_type == "get_status":
                        status = _paper_engine.get_status()
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "status_update",
                                "data": status,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    elif msg_type == "get_positions":
                        positions = _paper_engine.ledger.list_positions()
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "positions_update",
                                "data": [p.to_dict() for p in positions],
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    elif msg_type == "step_engine":
                        # Trigger a single engine step
                        results = _paper_engine.step_all_active_profiles()
                        await ws_manager.send_personal(
                            websocket,
                            {
                                "type": "engine_step_result",
                                "data": results,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        # Broadcast updated status to all
                        status = _paper_engine.get_status()
                        await ws_manager.broadcast(
                            {
                                "type": "status_update",
                                "data": status,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
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
