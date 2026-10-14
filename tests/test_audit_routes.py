"""
Tests for Audit Log Routes (src/routes/audit.py):
- GET /api/audit - query audit logs with filters
- GET /api/audit/event-types - list all event types
- GET /api/audit/stats - audit statistics (stub)
- AuditLogger functionality (log, query)
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.audit.logger import AuditEvent, AuditEventType, AuditLogger
from src.auth.dependencies import require_audit_read
from src.routes.audit import router as audit_router


class TestAuditRoutesList:
    """Tests for GET /api/audit endpoint."""

    def test_list_audit_logs_empty(self, client, mock_audit_logger):
        """Should return empty list when no logs exist."""
        mock_audit_logger.query = Mock(return_value=[])

        response = client.get("/api/audit")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["limit"] == 100
        assert data["offset"] == 0
        assert data["logs"] == []

    def test_list_audit_logs_with_data(self, client, mock_audit_logger):
        """Should return audit logs with all fields."""
        mock_logs = [
            {
                "event_id": "evt_abc123",
                "timestamp": "2024-01-01T12:00:00",
                "event_type": "auth.login",
                "user_id": "user_1",
                "username": "testuser",
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
                "resource_id": None,
                "resource_type": None,
                "action": "login",
                "details": {},
                "success": True,
                "error_message": None,
            }
        ]
        mock_audit_logger.query = Mock(return_value=mock_logs)

        response = client.get("/api/audit")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["logs"][0]["event_id"] == "evt_abc123"
        assert data["logs"][0]["event_type"] == "auth.login"
        assert data["logs"][0]["success"] is True

    def test_list_audit_logs_with_user_filter(self, client, mock_audit_logger):
        """Should filter by user_id."""
        mock_audit_logger.query = Mock(return_value=[])

        response = client.get("/api/audit?user_id=user_1")

        assert response.status_code == 200
        mock_audit_logger.query.assert_called_once()
        call_args = mock_audit_logger.query.call_args
        assert call_args.kwargs["user_id"] == "user_1"

    def test_list_audit_logs_with_event_type_filter(self, client, mock_audit_logger):
        """Should filter by event_type."""
        mock_audit_logger.query = Mock(return_value=[])

        response = client.get("/api/audit?event_type=auth.login")

        assert response.status_code == 200
        call_args = mock_audit_logger.query.call_args
        assert call_args.kwargs["event_type"].value == "auth.login"

    def test_list_audit_logs_with_time_filters(self, client, mock_audit_logger):
        """Should filter by start_time and end_time."""
        mock_audit_logger.query = Mock(return_value=[])

        start = "2024-01-01T00:00:00"
        end = "2024-01-02T00:00:00"
        response = client.get(f"/api/audit?start_time={start}&end_time={end}")

        assert response.status_code == 200
        call_args = mock_audit_logger.query.call_args
        assert call_args.kwargs["start_time"] is not None
        assert call_args.kwargs["end_time"] is not None

    def test_list_audit_logs_with_pagination(self, client, mock_audit_logger):
        """Should respect limit and offset parameters."""
        mock_audit_logger.query = Mock(return_value=[])

        response = client.get("/api/audit?limit=50&offset=25")

        assert response.status_code == 200
        call_args = mock_audit_logger.query.call_args
        assert call_args.kwargs["limit"] == 50
        assert call_args.kwargs["offset"] == 25

    def test_list_audit_logs_invalid_event_type(self, client):
        """Should return 400 for invalid event_type."""
        response = client.get("/api/audit?event_type=invalid.type")

        assert response.status_code == 400
        assert "invalid event_type" in response.json()["detail"].lower()

    def test_list_audit_logs_invalid_datetime_format(self, client):
        """Should return 500 for invalid datetime format."""
        response = client.get("/api/audit?start_time=invalid-date")

        assert response.status_code == 500
        assert "failed to query audit logs" in response.json()["detail"].lower()

    def test_list_audit_logs_limit_validation_min(self, client):
        """Should reject limit < 1."""
        response = client.get("/api/audit?limit=0")
        assert response.status_code == 422

    def test_list_audit_logs_limit_validation_max(self, client):
        """Should reject limit > 1000."""
        response = client.get("/api/audit?limit=1001")
        assert response.status_code == 422

    def test_list_audit_logs_offset_validation(self, client):
        """Should reject offset < 0."""
        response = client.get("/api/audit?offset=-1")
        assert response.status_code == 422

    def test_list_audit_logs_requires_audit_read_permission(self):
        """Should require audit read permission."""
        from fastapi import FastAPI

        from src.routes.audit import router as audit_router

        app = FastAPI()
        app.include_router(audit_router)
        # No auth override
        raw_client = TestClient(app)

        response = raw_client.get("/api/audit")
        # Should return 401 (unauthorized) or 403 (forbidden)
        assert response.status_code in (401, 403)


class TestAuditRoutesEventTypes:
    """Tests for GET /api/audit/event-types endpoint."""

    def test_list_event_types(self, client):
        """Should return all audit event types."""
        response = client.get("/api/audit/event-types")

        assert response.status_code == 200
        data = response.json()
        assert "event_types" in data
        assert isinstance(data["event_types"], list)
        assert len(data["event_types"]) > 0
        # Check some known event types
        assert "auth.login" in data["event_types"]
        assert "strategy.created" in data["event_types"]
        assert "paper.trade_executed" in data["event_types"]


class TestAuditRoutesStats:
    """Tests for GET /api/audit/stats endpoint."""

    def test_get_audit_stats(self, client):
        """Should return stats stub response."""
        response = client.get("/api/audit/stats")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "Statistics endpoint not yet implemented" in data["message"]
        assert "suggested_filters" in data


class TestAuditLoggerCore:
    """Tests for AuditLogger core functionality."""

    @pytest.fixture
    def audit_logger(self, tmp_path):
        """Create AuditLogger with temporary database."""
        db_path = tmp_path / "test_audit.db"
        from src.database import CandleDatabase

        db = CandleDatabase(db_path=str(db_path))
        return AuditLogger(db=db)

    def test_log_basic_event(self, audit_logger):
        """Should log basic audit event."""
        event = audit_logger.log(
            event_type=AuditEventType.LOGIN,
            user_id="user_1",
            username="testuser",
            action="login",
        )

        assert event.event_type == AuditEventType.LOGIN
        assert event.user_id == "user_1"
        assert event.username == "testuser"
        assert event.action == "login"
        assert event.success is True

        # Verify it was persisted
        logs = audit_logger.query(limit=1)
        assert len(logs) == 1
        assert logs[0]["event_id"] == event.event_id

    def test_log_event_with_details(self, audit_logger):
        """Should log event with details dict."""
        details = {"key": "value", "count": 42}
        event = audit_logger.log(
            event_type=AuditEventType.STRATEGY_CREATED,
            user_id="user_1",
            details=details,
        )

        assert event.details == details

        logs = audit_logger.query(limit=1)
        assert logs[0]["details"] == details

    def test_log_failed_event(self, audit_logger):
        """Should log failed event with error message."""
        event = audit_logger.log(
            event_type=AuditEventType.LOGIN_FAILED,
            user_id="user_1",
            success=False,
            error_message="Invalid password",
        )

        assert event.success is False
        assert event.error_message == "Invalid password"

        logs = audit_logger.query(limit=1)
        assert logs[0]["success"] is False
        assert logs[0]["error_message"] == "Invalid password"

    def test_log_with_resource(self, audit_logger):
        """Should log event with resource info."""
        event = audit_logger.log(
            event_type=AuditEventType.STRATEGY_DEPLOYED,
            user_id="user_1",
            resource_id="strat_123",
            resource_type="strategy",
            action="deployed",
        )

        assert event.resource_id == "strat_123"
        assert event.resource_type == "strategy"

        logs = audit_logger.query(limit=1)
        assert logs[0]["resource_id"] == "strat_123"
        assert logs[0]["resource_type"] == "strategy"

    def test_query_filters_by_user_id(self, audit_logger):
        """Should filter query by user_id."""
        audit_logger.log(AuditEventType.LOGIN, user_id="user_1")
        audit_logger.log(AuditEventType.LOGIN, user_id="user_2")

        logs = audit_logger.query(user_id="user_1")
        assert len(logs) == 1
        assert logs[0]["user_id"] == "user_1"

    def test_query_filters_by_event_type(self, audit_logger):
        """Should filter query by event_type."""
        audit_logger.log(AuditEventType.LOGIN)
        audit_logger.log(AuditEventType.LOGOUT)

        logs = audit_logger.query(event_type=AuditEventType.LOGIN)
        assert len(logs) == 1
        assert logs[0]["event_type"] == "auth.login"

    def test_query_filters_by_time_range(self, audit_logger):
        """Should filter query by time range."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=2)
        future = now + timedelta(hours=2)

        audit_logger.log(AuditEventType.LOGIN)  # Now

        # Query with future start time should return empty
        logs = audit_logger.query(start_time=future)
        assert len(logs) == 0

        # Query with past start time should return the log
        logs = audit_logger.query(start_time=past)
        assert len(logs) == 1

    def test_query_respects_limit_and_offset(self, audit_logger):
        """Should respect limit and offset in query."""
        for i in range(5):
            audit_logger.log(AuditEventType.LOGIN, username=f"user_{i}")

        logs = audit_logger.query(limit=2, offset=1)
        assert len(logs) == 2
        # Should be ordered by timestamp DESC
        assert logs[0]["username"] == "user_3"  # 4th created (0-indexed: 3)
        assert logs[1]["username"] == "user_2"

    def test_query_returns_empty_for_no_matches(self, audit_logger):
        """Should return empty list for no matches."""
        audit_logger.log(AuditEventType.LOGIN, user_id="user_1")

        logs = audit_logger.query(user_id="nonexistent")
        assert logs == []


class TestAuditLoggerHelpers:
    """Tests for AuditLogger helper methods."""

    def test_log_login(self, audit_logger):
        """Should log login attempt."""
        event = audit_logger.log_login(
            user_id="user_1",
            username="testuser",
            ip="192.168.1.1",
            success=True,
        )

        assert event.event_type == AuditEventType.LOGIN
        assert event.action == "login"
        assert event.success is True

        event = audit_logger.log_login(
            user_id="user_1",
            username="testuser",
            ip="192.168.1.1",
            success=False,
            error="Invalid password",
        )

        assert event.event_type == AuditEventType.LOGIN_FAILED
        assert event.success is False
        assert event.error_message == "Invalid password"

    def test_log_api_key_created(self, audit_logger):
        """Should log API key creation."""
        event = audit_logger.log_api_key_created(
            user_id="user_1",
            username="testuser",
            key_name="my-key",
            key_prefix="ema_abc",
        )

        assert event.event_type == AuditEventType.API_KEY_CREATED
        assert event.action == "api_key_created"
        assert event.resource_type == "api_key"
        assert event.details["key_name"] == "my-key"
        assert event.details["key_prefix"] == "ema_abc"

    def test_log_strategy_change(self, audit_logger):
        """Should log strategy change."""
        event = audit_logger.log_strategy_change(
            user_id="user_1",
            username="testuser",
            event_type=AuditEventType.STRATEGY_DEPLOYED,
            strategy_id="strat_123",
            details={"mode": "paper"},
        )

        assert event.event_type == AuditEventType.STRATEGY_DEPLOYED
        assert event.resource_id == "strat_123"
        assert event.resource_type == "strategy"
        assert event.action == "deployed"
        assert event.details["mode"] == "paper"

    def test_log_paper_trade(self, audit_logger):
        """Should log paper trade."""
        trade_data = {"symbol": "BTC/USD", "side": "buy", "qty": 0.1}
        event = audit_logger.log_paper_trade("user_1", "testuser", trade_data)

        assert event.event_type == AuditEventType.PAPER_TRADE_EXECUTED
        assert event.resource_type == "paper_trade"
        assert event.details == trade_data

    def test_log_order_execution(self, audit_logger):
        """Should log order execution."""
        order_data = {"order_id": "ord_123", "symbol": "ETH/USD", "side": "sell"}
        event = audit_logger.log_order_execution("user_1", "testuser", order_data)

        assert event.event_type == AuditEventType.ORDER_EXECUTED
        assert event.resource_type == "order"
        assert event.details == order_data


class TestAuditEventDataclass:
    """Tests for AuditEvent dataclass."""

    def test_to_dict(self):
        """Should convert to dict with all fields."""
        event = AuditEvent(
            event_id="evt_123",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            event_type=AuditEventType.LOGIN,
            user_id="user_1",
            username="testuser",
            action="login",
            details={"key": "value"},
            success=True,
        )

        d = event.to_dict()
        assert d["event_id"] == "evt_123"
        assert d["timestamp"] == "2024-01-01T12:00:00+00:00"
        assert d["event_type"] == "auth.login"
        assert d["user_id"] == "user_1"
        assert d["username"] == "testuser"
        assert d["action"] == "login"
        assert d["details"] == {"key": "value"}
        assert d["success"] is True

    def test_to_json(self):
        """Should convert to JSON string."""
        event = AuditEvent(
            event_id="evt_123",
            event_type=AuditEventType.LOGIN,
            action="login",
        )

        json_str = event.to_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["event_id"] == "evt_123"
        assert parsed["event_type"] == "auth.login"


class TestAuditEventType:
    """Tests for AuditEventType enum."""

    def test_all_event_types_have_values(self):
        """All event types should have string values."""
        for e in AuditEventType:
            assert isinstance(e.value, str)
            assert len(e.value) > 0

    def test_event_type_categories(self):
        """Should have expected categories."""
        auth_events = [e for e in AuditEventType if e.value.startswith("auth.")]
        strategy_events = [e for e in AuditEventType if e.value.startswith("strategy.")]
        paper_events = [e for e in AuditEventType if e.value.startswith("paper.")]

        assert len(auth_events) >= 5
        assert len(strategy_events) >= 5
        assert len(paper_events) >= 5


class TestAuditRoutesIntegration:
    """Integration tests for audit routes with real FastAPI app."""

    def test_full_audit_flow(self, client, mock_audit_logger):
        """Test complete audit flow: log -> query -> event-types."""
        # Log some events
        mock_logs = [
            {
                "event_id": f"evt_{i}",
                "timestamp": "2024-01-01T12:00:00",
                "event_type": "auth.login",
                "user_id": "user_1",
                "username": "testuser",
                "ip_address": "192.168.1.1",
                "user_agent": None,
                "resource_id": None,
                "resource_type": None,
                "action": "login",
                "details": {},
                "success": True,
                "error_message": None,
            }
            for i in range(3)
        ]
        mock_audit_logger.query = Mock(return_value=mock_logs)

        # Query logs
        response = client.get("/api/audit")
        assert response.status_code == 200
        assert response.json()["total"] == 3

        # Get event types
        response = client.get("/api/audit/event-types")
        assert response.status_code == 200
        assert "auth.login" in response.json()["event_types"]

        # Get stats
        response = client.get("/api/audit/stats")
        assert response.status_code == 200
        assert "not yet implemented" in response.json()["message"].lower()


# Fixtures
@pytest.fixture
def audit_logger(tmp_path):
    """Create AuditLogger with temporary database."""
    db_path = tmp_path / "test_audit.db"
    from src.database import CandleDatabase

    db = CandleDatabase(db_path=str(db_path))
    return AuditLogger(db=db)


@pytest.fixture
def mock_audit_logger():
    """Create a mock audit logger."""
    mock_logger = Mock(spec=AuditLogger)
    mock_logger.query = Mock(return_value=[])
    return mock_logger


@pytest.fixture
def client(mock_audit_logger):
    """Create test client with audit router and auth override."""
    from fastapi import FastAPI

    from src.routes.audit import get_audit_logger

    app = FastAPI()
    app.include_router(audit_router)

    # Override auth dependency for testing
    async def override_require_audit_read():
        # Return a mock user with audit read permission
        class MockUser:
            is_active = True
            is_superuser = True
            role = "admin"

            def has_permission(self, perm):
                return True

        return MockUser()

    app.dependency_overrides[require_audit_read] = override_require_audit_read

    # Override the audit logger dependency
    def override_get_audit_logger():
        return mock_audit_logger

    app.dependency_overrides[get_audit_logger] = override_get_audit_logger

    return TestClient(app)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
