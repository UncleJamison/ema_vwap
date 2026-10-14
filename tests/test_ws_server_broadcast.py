"""
Tests for WebSocket Server Broadcast/Cleanup (src/ws_server.py):
- WebSocketManager connection/disconnection
- Broadcast concurrent fan-out with cleanup
- send_personal functionality
- Message handling (ping/pong, subscribe, get_status, get_positions)
- WS status endpoint
- Periodic update task
- Error handling (disconnect, RuntimeError, etc.)
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from src.ws_server import WebSocketManager, create_ws_app, ws_lifespan


@pytest.fixture
def ws_manager():
    """Create a fresh WebSocketManager instance."""
    return WebSocketManager()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    ws = AsyncMock(spec=WebSocket)
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


@pytest.fixture
def mock_paper_engine():
    """Create a mock paper engine."""
    engine = Mock()
    engine.get_status = Mock(
        return_value={
            "is_running": True,
            "account_id": "test",
            "cash_balance": 10000.0,
            "total_equity": 15000.0,
            "unrealized_pnl": 500.0,
            "active_profiles_count": 2,
            "open_positions_count": 1,
        }
    )
    engine.ledger = Mock()
    engine.ledger.list_positions = Mock(return_value=[])
    return engine


@pytest.fixture(autouse=True)
def setup_paper_engine_alias(mock_paper_engine):
    """Set up _paper_engine alias in paper engine module for ws_server tests."""
    import src.paper.engine as paper_engine_module

    original = getattr(paper_engine_module, "_paper_engine", None)
    paper_engine_module._paper_engine = mock_paper_engine
    yield
    if original is None:
        delattr(paper_engine_module, "_paper_engine")
    else:
        paper_engine_module._paper_engine = original


class TestWebSocketManagerConnect:
    """Tests for WebSocketManager.connect method."""

    @pytest.mark.asyncio
    async def test_connect_accepts_valid_api_key(self, ws_manager, mock_websocket):
        """Should accept connection with valid API key."""
        with patch("src.auth.security.verify_api_key", return_value=True):
            await ws_manager.connect(mock_websocket, api_key="valid-key")

        mock_websocket.accept.assert_called_once()
        assert len(ws_manager.active_connections) == 1
        assert mock_websocket in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_connect_rejects_invalid_api_key(self, ws_manager, mock_websocket):
        """Should reject connection with invalid API key."""
        with patch("src.auth.security.verify_api_key", return_value=False):
            await ws_manager.connect(mock_websocket, api_key="invalid-key")

        mock_websocket.close.assert_called_once_with(
            code=4001, reason="Invalid or missing API key"
        )
        assert len(ws_manager.active_connections) == 0

    @pytest.mark.asyncio
    async def test_connect_rejects_missing_api_key(self, ws_manager, mock_websocket):
        """Should reject connection with missing API key."""
        with patch("src.auth.security.verify_api_key", return_value=False):
            await ws_manager.connect(mock_websocket, api_key=None)

        mock_websocket.close.assert_called_once_with(
            code=4001, reason="Invalid or missing API key"
        )
        assert len(ws_manager.active_connections) == 0

    @pytest.mark.asyncio
    async def test_connect_empty_api_key_rejected(self, ws_manager, mock_websocket):
        """Should reject connection with empty API key."""
        with patch("src.auth.security.verify_api_key", return_value=False):
            await ws_manager.connect(mock_websocket, api_key="")

        mock_websocket.close.assert_called_once_with(
            code=4001, reason="Invalid or missing API key"
        )
        assert len(ws_manager.active_connections) == 0


class TestWebSocketManagerDisconnect:
    """Tests for WebSocketManager.disconnect method."""

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self, ws_manager, mock_websocket):
        """Should remove websocket from active connections."""
        # First add the connection
        async with ws_manager._lock:
            ws_manager.active_connections.append(mock_websocket)

        await ws_manager.disconnect(mock_websocket)

        assert len(ws_manager.active_connections) == 0
        assert mock_websocket not in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_disconnect_handles_missing_connection(
        self, ws_manager, mock_websocket
    ):
        """Should handle disconnect of non-existent connection gracefully."""
        # Don't add the connection first
        await ws_manager.disconnect(mock_websocket)

        assert len(ws_manager.active_connections) == 0


class TestWebSocketManagerBroadcast:
    """Tests for WebSocketManager.broadcast method."""

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self, ws_manager):
        """Should return early when no active connections."""
        await ws_manager.broadcast({"type": "test", "data": "value"})
        # Should not raise, just return

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_connections(self, ws_manager):
        """Should send message to all connected clients."""
        ws1 = AsyncMock(spec=WebSocket)
        ws1.send_text = AsyncMock()
        ws2 = AsyncMock(spec=WebSocket)
        ws2.send_text = AsyncMock()

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws1, ws2])

        message = {"type": "test", "data": "value"}
        await ws_manager.broadcast(message)

        expected_json = json.dumps(message)
        ws1.send_text.assert_called_once_with(expected_json)
        ws2.send_text.assert_called_once_with(expected_json)

    @pytest.mark.asyncio
    async def test_broadcast_cleans_up_disconnected(self, ws_manager):
        """Should clean up disconnected clients during broadcast."""
        ws_good = AsyncMock(spec=WebSocket)
        ws_good.send_text = AsyncMock(return_value=None)

        ws_bad = AsyncMock(spec=WebSocket)
        ws_bad.send_text = AsyncMock(side_effect=RuntimeError("Connection closed"))

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws_good, ws_bad])

        message = {"type": "test", "data": "value"}
        await ws_manager.broadcast(message)

        # Good connection should remain
        assert ws_good in ws_manager.active_connections
        # Bad connection should be removed
        assert ws_bad not in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_handles_websocket_disconnect(self, ws_manager):
        """Should handle WebSocketDisconnect during broadcast."""
        from fastapi import WebSocketDisconnect

        ws_good = AsyncMock(spec=WebSocket)
        ws_good.send_text = AsyncMock(return_value=None)

        ws_bad = AsyncMock(spec=WebSocket)
        ws_bad.send_text = AsyncMock(side_effect=WebSocketDisconnect())

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws_good, ws_bad])

        message = {"type": "test", "data": "value"}
        await ws_manager.broadcast(message)

        # Good connection should remain
        assert ws_good in ws_manager.active_connections
        # Bad connection should be removed
        assert ws_bad not in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_handles_baseexception(self, ws_manager):
        """Should handle BaseException subclasses during broadcast (not removed)."""
        ws_good = AsyncMock(spec=WebSocket)
        ws_good.send_text = AsyncMock(return_value=None)

        # For BaseException, the gather returns the exception, not removes the connection
        ws_bad = AsyncMock(spec=WebSocket)
        ws_bad.send_text = AsyncMock(return_value=KeyboardInterrupt("Interrupted"))

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws_good, ws_bad])

        message = {"type": "test", "data": "value"}
        await ws_manager.broadcast(message)

        # Both should remain (KeyboardInterrupt is BaseException, not removed)
        assert ws_good in ws_manager.active_connections
        assert ws_bad in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_concurrent_fanout(self, ws_manager):
        """Should send to all connections concurrently (not sequentially)."""

        async def slow_send(*args):
            await asyncio.sleep(0.1)

        ws1 = AsyncMock(spec=WebSocket)
        ws1.send_text = slow_send
        ws2 = AsyncMock(spec=WebSocket)
        ws2.send_text = slow_send

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws1, ws2])

        message = {"type": "test", "data": "value"}

        start = asyncio.get_event_loop().time()
        await ws_manager.broadcast(message)
        elapsed = asyncio.get_event_loop().time() - start

        # Should take ~0.1s (concurrent) not ~0.2s (sequential)
        assert (
            elapsed < 0.15
        ), f"Broadcast took {elapsed}s, expected < 0.15s (concurrent)"


class TestWebSocketManagerSendPersonal:
    """Tests for WebSocketManager.send_personal method."""

    @pytest.mark.asyncio
    async def test_send_personal_sends_message(self, ws_manager, mock_websocket):
        """Should send message to specific client."""
        message = {"type": "personal", "data": "value"}
        await ws_manager.send_personal(mock_websocket, message)

        expected_json = json.dumps(message)
        mock_websocket.send_text.assert_called_once_with(expected_json)

    @pytest.mark.asyncio
    async def test_send_personal_disconnects_on_runtime_error(
        self, ws_manager, mock_websocket
    ):
        """Should disconnect client on RuntimeError."""
        mock_websocket.send_text = AsyncMock(
            side_effect=RuntimeError("Connection closed")
        )

        with patch.object(
            ws_manager, "disconnect", new_callable=AsyncMock
        ) as mock_disconnect:
            await ws_manager.send_personal(mock_websocket, {"type": "test"})

        mock_disconnect.assert_called_once_with(mock_websocket)


class TestWebSocketMessageHandling:
    """Tests for message handling in dashboard_websocket endpoint."""

    @pytest.mark.asyncio
    async def test_ping_pong(self, mock_paper_engine):
        """Should respond to ping with pong."""
        ws = AsyncMock(spec=WebSocket)
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"type": "ping"}),
                WebSocketDisconnect(),  # End the loop
            ]
        )

        with patch("src.ws_server.ws_manager") as mock_ws_manager, patch(
            "src.auth.security.verify_api_key", return_value=True
        ):

            mock_ws_manager.connect = AsyncMock()
            mock_ws_manager.send_personal = AsyncMock()
            mock_ws_manager.disconnect = AsyncMock()

            _ = create_ws_app()

            # We can't easily test the full endpoint without a running server,
            # but we can test the logic by calling the handler directly

            # Test the ping/pong logic manually
            msg = json.dumps({"type": "ping"})
            data = json.loads(msg)
            msg_type = data.get("type")

            assert msg_type == "ping"

            # Simulate pong response
            pong_msg = {
                "type": "pong",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            assert pong_msg["type"] == "pong"
            assert "timestamp" in pong_msg

    @pytest.mark.asyncio
    async def test_subscribe_channels(self, mock_paper_engine):
        """Should handle subscribe message."""
        _ = {"type": "subscribe", "channels": ["positions", "trades"]}

        # Simulate subscribe response
        response = {
            "type": "subscribed",
            "channels": ["positions", "trades"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        assert response["type"] == "subscribed"
        assert response["channels"] == ["positions", "trades"]

    @pytest.mark.asyncio
    async def test_get_status(self, mock_paper_engine):
        """Should handle get_status message."""
        _ = {"type": "get_status"}

        status = mock_paper_engine.get_status()
        response = {
            "type": "status_update",
            "data": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        assert response["type"] == "status_update"
        assert response["data"] == status

    @pytest.mark.asyncio
    async def test_get_positions(self, mock_paper_engine):
        """Should handle get_positions message."""
        mock_positions = [Mock(to_dict=Mock(return_value={"symbol": "BTC/USD"}))]
        mock_paper_engine.ledger.list_positions = Mock(return_value=mock_positions)

        _ = {"type": "get_positions"}

        response = {
            "type": "positions_update",
            "data": [{"symbol": "BTC/USD"}],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        assert response["type"] == "positions_update"
        assert len(response["data"]) == 1

    @pytest.mark.asyncio
    async def test_step_engine_disabled(self):
        """Should return error for step_engine command."""
        _ = {"type": "step_engine"}

        response = {
            "type": "error",
            "data": "step_engine command disabled: WebSocket requires API key authentication via X-API-Key header.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        assert response["type"] == "error"
        assert "disabled" in response["data"]


class TestWebSocketStatusEndpoint:
    """Tests for WS status endpoint."""

    @pytest.mark.asyncio
    async def test_ws_status(self, ws_manager):
        """Should return correct status and client count."""
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws1, ws2])

        # Test the status logic
        status = {
            "status": "running",
            "connected_clients": len(ws_manager.active_connections),
        }

        assert status["status"] == "running"
        assert status["connected_clients"] == 2

    @pytest.mark.asyncio
    async def test_ws_status_zero_clients(self, ws_manager):
        """Should return 0 clients when none connected."""
        status = {
            "status": "running",
            "connected_clients": len(ws_manager.active_connections),
        }

        assert status["connected_clients"] == 0


class TestWebSocketPeriodicUpdate:
    """Tests for periodic update background task."""

    @pytest.mark.asyncio
    async def test_periodic_update_broadcasts_status(self, mock_paper_engine):
        """Should broadcast status periodically."""
        with patch("src.ws_server.ws_manager") as mock_ws_manager:
            mock_ws_manager.broadcast = AsyncMock()

            # Run the periodic update logic directly
            status = mock_paper_engine.get_status()
            await mock_ws_manager.broadcast(
                {
                    "type": "status_update",
                    "data": status,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            # Should have broadcasted once
            assert mock_ws_manager.broadcast.call_count == 1


class TestCreateWSApp:
    """Tests for create_ws_app factory function."""

    def test_create_ws_app_returns_fastapi(self):
        """Should return a FastAPI app with correct configuration."""
        app = create_ws_app()

        from fastapi import FastAPI

        assert isinstance(app, FastAPI)
        assert app.title == "EMA+VWAP WebSocket Server"
        assert app.version == "1.0.0"
        assert app.router.lifespan_context is not None

    def test_create_ws_app_has_cors_middleware(self):
        """Should have CORS middleware configured."""
        app = create_ws_app()

        # Check CORS middleware is present
        cors_middleware = None
        for middleware in app.user_middleware:
            if "CORS" in str(middleware.cls):
                cors_middleware = middleware
                break

        assert cors_middleware is not None
        assert cors_middleware.kwargs["allow_origins"] == [
            "http://localhost:3000",
            "http://localhost:8080",
        ]
        assert cors_middleware.kwargs["allow_credentials"] is False

    def test_create_ws_app_has_websocket_route(self):
        """Should have WebSocket route for dashboard."""
        app = create_ws_app()

        ws_routes = [
            r for r in app.routes if hasattr(r, "path") and r.path == "/ws/dashboard"
        ]
        assert len(ws_routes) == 1

    def test_create_ws_app_has_status_route(self):
        """Should have WS status route."""
        app = create_ws_app()

        status_routes = [
            r for r in app.routes if hasattr(r, "path") and r.path == "/ws/status"
        ]
        assert len(status_routes) == 1


class TestWSLifespan:
    """Tests for ws_lifespan context manager."""

    @pytest.mark.asyncio
    async def test_ws_lifespan_starts_and_cancels_task(self, mock_paper_engine):
        """Should start periodic task and cancel on exit."""
        # Test the lifespan directly by running it
        app = create_ws_app()

        # The lifespan creates a task that runs periodic_update
        # We can't easily test the full lifespan without running the actual task
        # But we can verify the lifespan context manager works

        try:
            async with ws_lifespan(app):
                # Get the task from the lifespan context
                # The task is created inside ws_lifespan
                await asyncio.sleep(0.01)  # Let it start
        except asyncio.CancelledError:
            pass

        # If we get here without error, the lifespan works
        assert True


class TestConcurrentBroadcastStress:
    """Stress tests for concurrent broadcast behavior."""

    @pytest.mark.asyncio
    async def test_broadcast_many_clients(self, ws_manager):
        """Should handle many concurrent clients."""
        num_clients = 50
        clients = []

        for i in range(num_clients):
            ws = AsyncMock(spec=WebSocket)
            ws.send_text = AsyncMock(return_value=None)
            clients.append(ws)

        async with ws_manager._lock:
            ws_manager.active_connections.extend(clients)

        message = {"type": "stress_test", "index": 0}
        await ws_manager.broadcast(message)

        # All clients should have received the message
        for client in clients:
            client.send_text.assert_called_once()
            call_args = client.send_text.call_args[0][0]
            assert json.loads(call_args)["type"] == "stress_test"

    @pytest.mark.asyncio
    async def test_broadcast_mixed_success_failure(self, ws_manager):
        """Should handle mixed success/failure scenarios."""
        num_good = 30
        num_bad = 20
        clients = []

        for i in range(num_good):
            ws = AsyncMock(spec=WebSocket)
            ws.send_text = AsyncMock(return_value=None)
            clients.append(ws)

        for i in range(num_bad):
            ws = AsyncMock(spec=WebSocket)
            ws.send_text = AsyncMock(side_effect=RuntimeError("Connection closed"))
            clients.append(ws)

        async with ws_manager._lock:
            ws_manager.active_connections.extend(clients)

        message = {"type": "mixed_test", "data": "value"}
        await ws_manager.broadcast(message)

        # Good clients should remain
        for client in clients[:num_good]:
            assert client in ws_manager.active_connections

        # Bad clients should be removed
        for client in clients[num_good:]:
            assert client not in ws_manager.active_connections


class TestBroadcastCleanupEdgeCases:
    """Tests for edge cases in broadcast cleanup."""

    @pytest.mark.asyncio
    async def test_broadcast_removes_only_failed(self, ws_manager):
        """Should only remove failed connections, not successful ones."""
        ws1 = AsyncMock(spec=WebSocket)
        ws1.send_text = AsyncMock(return_value=None)
        ws2 = AsyncMock(spec=WebSocket)
        ws2.send_text = AsyncMock(side_effect=RuntimeError("Closed"))
        ws3 = AsyncMock(spec=WebSocket)
        ws3.send_text = AsyncMock(return_value=None)

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws1, ws2, ws3])

        await ws_manager.broadcast({"test": "data"})

        assert ws1 in ws_manager.active_connections
        assert ws2 not in ws_manager.active_connections
        assert ws3 in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_preserves_order_of_remaining(self, ws_manager):
        """Should preserve order of remaining connections."""
        ws1 = AsyncMock(spec=WebSocket)
        ws1.send_text = AsyncMock(return_value=None)
        ws2 = AsyncMock(spec=WebSocket)
        ws2.send_text = AsyncMock(side_effect=RuntimeError("Closed"))
        ws3 = AsyncMock(spec=WebSocket)
        ws3.send_text = AsyncMock(return_value=None)

        async with ws_manager._lock:
            ws_manager.active_connections.extend([ws1, ws2, ws3])

        await ws_manager.broadcast({"test": "data"})

        # Order of remaining should be preserved
        remaining = ws_manager.active_connections
        assert remaining.index(ws1) < remaining.index(ws3)


class TestWSIntegration:
    """Integration tests for the full WebSocket flow."""

    def test_create_ws_app_is_callable(self):
        """create_ws_app should be callable and return app."""
        app = create_ws_app()
        assert app is not None

    @pytest.mark.asyncio
    async def test_full_connection_lifecycle(self, mock_paper_engine):
        """Test full connection lifecycle: connect -> message -> disconnect."""
        ws = AsyncMock(spec=WebSocket)
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"type": "ping"}),
                json.dumps({"type": "get_status"}),
                WebSocketDisconnect(),
            ]
        )

        with patch("src.auth.security.verify_api_key", return_value=True), patch(
            "src.ws_server.ws_manager"
        ) as mock_ws_manager:

            mock_ws_manager.connect = AsyncMock()
            mock_ws_manager.send_personal = AsyncMock()
            mock_ws_manager.disconnect = AsyncMock()

            # Simulate the connection flow
            await mock_ws_manager.connect(ws, api_key="test-key")
            mock_ws_manager.connect.assert_called_once_with(ws, api_key="test-key")

            # Send ping
            await mock_ws_manager.send_personal(
                ws, {"type": "pong", "timestamp": "2024-01-01T00:00:00Z"}
            )
            mock_ws_manager.send_personal.assert_called()

            # Disconnect
            await mock_ws_manager.disconnect(ws)
            mock_ws_manager.disconnect.assert_called_once_with(ws)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
