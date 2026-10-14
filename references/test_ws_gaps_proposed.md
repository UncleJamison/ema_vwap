"""Proposed isolated tests for src/ws_server.py — do not edit source; mock engine/DB."""

test_ws_manager_connect_disconnect = "WebSocketManager.connect/disconnect with mock WebSocket; assert active_connections count; assert lock used."

test_ws_manager_broadcast_empty = "broadcast with empty active_connections -> no-op; no send_text called."

test_ws_manager_broadcast_removes_dead = "broadcast catches RuntimeError on send_text, removes dead sockets from list (line 59-64)."

test_ws_manager_send_personal_runtime = "send_personal catches RuntimeError and calls disconnect (line 70-71)."

test_ws_lifespan_cancel = "ws_lifespan yields then cancels periodic_update; assert CancelledError handled (line 99-105)."

test_dashboard_websocket_ping_pong = "Client sends {\"type\":\"ping\"}; receive {\"type\":\"pong\"} (line 150-157). Mock _paper_engine."

test_dashboard_subscribe = "{\"type\":\"subscribe\",\"channels\":[\"pnl\"]} -> subscribed response (line 158-168)."

test_dashboard_get_status = "{\"type\":\"get_status\"} -> status_update using _paper_engine.get_status() (line 169-178)."

test_dashboard_get_positions = "{\"type\":\"get_positions\"} -> positions_update from ledger.list_positions() (line 179-188); mock ledger."

test_dashboard_step_engine = "{\"type\":\"step_engine\"} -> engine_step_result + broadcast status_update (line 189-208)."

test_ws_status_endpoint = "GET /ws/status returns running + len(ws_manager.active_connections) (line 223-229)."

# Gaps / bugs noted (file:line evidence)
gaps = {
    "no_ws_tests": "tests/ has zero ws_server references.",
    "global_singleton": "ws_manager = WebSocketManager() (line 74) shared across apps; tests must reset or monkeypatch.",
    "engine_import_in_endpoint": "dashboard_websocket imports src.paper.engine._paper_engine inside handler (line 131, 170, 191) — needs mock at import time or monkeypatch module.",
    "broadcast_no_json_exception_guard": "broadcast (line 53) does json.dumps without try/except; bad message crashes all clients.",
    "disconnect_missing_on_json_decode": "receive_text -> json.loads (line 147) raises JSONDecodeError; not caught -> connection drops without disconnect.",
    "subscribe_no_validation": "subscribe (line 158) accepts any channels list; no auth/filter.",
    "periodic_update_unbounded": "while True (line 83) with 5s sleep; no graceful shutdown except cancel.",
    "send_personal_disconnect_race": "send_personal (line 71) calls disconnect inside except; disconnect also acquires _lock; safe but redundant.",
}
