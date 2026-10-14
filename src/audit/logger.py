"""
Audit Logging (ema_vwap-108).

Provides comprehensive audit logging for all user actions, strategy changes,
trade executions, and configuration modifications.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from src.database import CandleDatabase
from src.db_connection_manager import get_database


class AuditEventType(str, Enum):
    """Types of audit events."""

    # Authentication
    LOGIN = "auth.login"
    LOGOUT = "auth.logout"
    LOGIN_FAILED = "auth.login_failed"
    PASSWORD_CHANGED = "auth.password_changed"
    API_KEY_CREATED = "auth.api_key_created"
    API_KEY_REVOKED = "auth.api_key_revoked"

    # User Management
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_ACTIVATED = "user.activated"
    USER_DEACTIVATED = "user.deactivated"

    # Strategy
    STRATEGY_CREATED = "strategy.created"
    STRATEGY_UPDATED = "strategy.updated"
    STRATEGY_DELETED = "strategy.deleted"
    STRATEGY_DEPLOYED = "strategy.deployed"
    STRATEGY_UNDEPLOYED = "strategy.undeployed"

    # Backtest
    BACKTEST_RUN = "backtest.run"
    BACKTEST_COMPLETED = "backtest.completed"
    BACKTEST_FAILED = "backtest.failed"

    # Optimization
    OPTIMIZATION_STARTED = "optimization.started"
    OPTIMIZATION_COMPLETED = "optimization.completed"
    OPTIMIZATION_FAILED = "optimization.failed"

    # Batch Optimization
    BATCH_STARTED = "batch.started"
    BATCH_COMPLETED = "batch.completed"
    BATCH_CANCELLED = "batch.cancelled"

    # Paper Trading
    PAPER_PROFILE_CREATED = "paper.profile_created"
    PAPER_PROFILE_UPDATED = "paper.profile_updated"
    PAPER_PROFILE_DELETED = "paper.profile_deleted"
    PAPER_ENGINE_STARTED = "paper.engine_started"
    PAPER_ENGINE_STOPPED = "paper.engine_stopped"
    PAPER_TRADE_EXECUTED = "paper.trade_executed"
    PAPER_POSITION_CLOSED = "paper.position_closed"

    # Portfolio
    PORTFOLIO_REBALANCED = "portfolio.rebalanced"
    PORTFOLIO_ORDER_ROUTED = "portfolio.order_routed"
    ORDER_EXECUTED = "order.executed"

    # Settings
    SETTINGS_UPDATED = "settings.updated"
    SETTINGS_RESET = "settings.reset"

    # System
    SYSTEM_DB_REPAIR = "system.db_repair"
    SYSTEM_DB_VACUUM = "system.db_vacuum"
    SYSTEM_CACHE_CLEARED = "system.cache_cleared"

    # API Key


@dataclass
class AuditEvent:
    """Audit event record."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    event_type: AuditEventType = AuditEventType.LOGIN
    user_id: str | None = None
    username: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None
    action: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "user_id": self.user_id,
            "username": self.username,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "action": self.action,
            "details": self.details,
            "success": self.success,
            "error_message": self.error_message,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class AuditLogger:
    """Thread-safe audit logger with database persistence."""

    def __init__(self, db: CandleDatabase):
        self.db = db
        self._lock = threading.Lock()
        self._ensure_table()
        self._logger = logging.getLogger("audit")
        self._logger.setLevel(logging.INFO)

    def _ensure_table(self):
        """Create audit log table if it doesn't exist."""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    user_id TEXT,
                    username TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    resource_id TEXT,
                    resource_type TEXT,
                    action TEXT,
                    details TEXT,
                    success INTEGER DEFAULT 1,
                    error_message TEXT
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type)
            """
            )
            conn.commit()

    def log(
        self,
        event_type: AuditEventType,
        user_id: str | None = None,
        username: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        resource_id: str | None = None,
        resource_type: str | None = None,
        action: str = "",
        details: dict[str, Any] | None = None,
        success: bool = True,
        error_message: str | None = None,
    ) -> AuditEvent:
        """Log an audit event."""
        event = AuditEvent(
            event_type=event_type,
            user_id=user_id,
            username=username,
            ip_address=ip_address,
            user_agent=user_agent,
            resource_id=resource_id,
            resource_type=resource_type,
            action=action,
            details=details or {},
            success=success,
            error_message=error_message,
        )

        # Persist to database
        with self._lock, self.db.get_connection() as conn:
            conn.execute(
                """
                    INSERT INTO audit_log (
                        event_id, timestamp, event_type, user_id, username,
                        ip_address, user_agent, resource_id, resource_type,
                        action, details, success, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event_type.value,
                    user_id,
                    username,
                    ip_address,
                    user_agent,
                    resource_id,
                    resource_type,
                    action,
                    json.dumps(details or {}),
                    int(success),
                    error_message,
                ),
            )
            conn.commit()

        # Also log to standard logger
        log_level = logging.INFO if success else logging.WARNING
        self._logger.log(
            log_level,
            f"AUDIT: {event_type.value} | user={username or user_id} | "
            f"action={action} | resource={resource_type}:{resource_id} | "
            f"success={success} | details={json.dumps(details or {})}",
        )

        return event

    def log_login(
        self,
        user_id: str,
        username: str,
        ip: str,
        success: bool,
        error: str | None = None,
    ):
        """Log login attempt."""
        return self.log(
            AuditEventType.LOGIN if success else AuditEventType.LOGIN_FAILED,
            user_id=user_id,
            username=username,
            ip_address=ip,
            action="login",
            success=success,
            error_message=error,
        )

    def log_api_key_created(
        self, user_id: str, username: str, key_name: str, key_prefix: str
    ):
        return self.log(
            AuditEventType.API_KEY_CREATED,
            user_id=user_id,
            username=username,
            action="api_key_created",
            resource_type="api_key",
            details={"key_name": key_name, "key_prefix": key_prefix},
        )

    def log_strategy_change(
        self,
        user_id: str,
        username: str,
        event_type: AuditEventType,
        strategy_id: str,
        details: dict | None = None,
    ):
        return self.log(
            event_type,
            user_id=user_id,
            username=username,
            resource_id=strategy_id,
            resource_type="strategy",
            action=event_type.value.split(".")[-1],
            details=details or {},
        )

    def log_paper_trade(self, user_id: str, username: str, trade_data: dict):
        return self.log(
            AuditEventType.PAPER_TRADE_EXECUTED,
            user_id=user_id,
            username=username,
            resource_type="paper_trade",
            action="executed",
            details=trade_data,
        )

    def log_order_execution(self, user_id: str, username: str, order_data: dict):
        return self.log(
            AuditEventType.ORDER_EXECUTED,
            user_id=user_id,
            username=username,
            resource_type="order",
            action="executed",
            details=order_data,
        )

    def query(
        self,
        user_id: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query audit log with filters."""
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type.value)

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time.isoformat())

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time.isoformat())

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.db.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def _row_to_dict(self, row) -> dict:
        return {
            "event_id": row[0],
            "timestamp": row[1],
            "event_type": row[2],
            "user_id": row[3],
            "username": row[4],
            "ip_address": row[5],
            "user_agent": row[6],
            "resource_id": row[7],
            "resource_type": row[8],
            "action": row[9],
            "details": json.loads(row[10]) if row[10] else {},
            "success": bool(row[11]),
            "error_message": row[12],
        }


# Global audit logger instance
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        db = get_database("system")
        _audit_logger = AuditLogger(db)
    return _audit_logger
