"""Tests for src/ws_server.py — isolated, no real network/production DB.

Core manager tests; endpoint tests avoid external imports.
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from src.ws_server import WebSocketManager, create_ws_app


def test_ws_manager_connect_disconnect():
    """WS connect/disconnect adds/removes from active_connections."""

    async def run():
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.send_text = AsyncMock()
        # Provide a valid API key for authentication
        import os

        os.environ["API_KEY"] = "test-key-for-tests"
        await mgr.connect(ws, api_key="test-key-for-tests")
        assert ws in mgr.active_connections
        await mgr.disconnect(ws)
        assert ws not in mgr.active_connections

    import asyncio

    asyncio.run(run())


def test_ws_manager_broadcast_empty():
    """Broadcast with no connections returns immediately."""

    async def run():
        mgr = WebSocketManager()
        await mgr.broadcast({"type": "ping"})
        assert len(mgr.active_connections) == 0

    import asyncio

    asyncio.run(run())


def test_ws_manager_broadcast_removes_dead():
    """Broadcast removes connections that raise RuntimeError."""

    async def run():
        mgr = WebSocketManager()
        ok_ws = MagicMock()
        ok_ws.send_text = AsyncMock(return_value=None)
        dead_ws = MagicMock()
        dead_ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))
        mgr.active_connections = [ok_ws, dead_ws]
        await mgr.broadcast({"type": "ping", "test": True})
        assert dead_ws not in mgr.active_connections
        assert ok_ws in mgr.active_connections

    import asyncio

    asyncio.run(run())


def test_ws_manager_send_personal_disconnects_on_error():
    """send_personal removes WS on RuntimeError."""

    async def run():
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))
        mgr.active_connections.append(ws)
        await mgr.send_personal(ws, {"type": "ping"})
        assert ws not in mgr.active_connections

    import asyncio

    asyncio.run(run())


def test_ws_manager_send_personal_success():
    """send_personal succeeds and keeps WS connected."""

    async def run():
        mgr = WebSocketManager()
        ws = MagicMock()
        ws.send_text = AsyncMock(return_value=None)
        mgr.active_connections.append(ws)
        await mgr.send_personal(ws, {"type": "ping"})
        assert ws in mgr.active_connections

    import asyncio

    asyncio.run(run())


def test_create_ws_app_endpoints_exist():
    app = create_ws_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ws/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert "connected_clients" in data


def test_ws_status_returns_connected_clients_count():
    import src.ws_server as ws_mod

    original = ws_mod.ws_manager
    mgr = WebSocketManager()
    ws_mod.ws_manager = mgr
    try:
        app = create_ws_app()
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/ws/status").json()["connected_clients"] == 0
    finally:
        ws_mod.ws_manager = original
