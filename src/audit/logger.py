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

from src.pg_database import save_audit_log_pg


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

    # Batch Operations
    BATCH_OPTIMIZATION_STARTED = "batch_optimization.started"
    BATCH_OPTIMIZATION_COMPLETED = "batch_optimization.completed"
    BATCH_OPTIMIZATION_FAILED = "batch_optimization.failed"

    # Paper Trading
    PAPER_TRADE_OPENED = "paper_trade.opened"
    PAPER_TRADE_CLOSED = "paper_trade.closed"
    PAPER_TRADE_EXECUTED = "paper_trade.executed"
    ORDER_EXECUTED = "order.executed"
    PAPER_POSITION_LIQUIDATED = "paper_position.liquidated"
    PAPER_ENGINE_STARTED = "paper_engine.started"
    PAPER_ENGINE_STOPPED = "paper_engine.stopped"
    PAPER_PROFILE_DEPLOYED = "paper_profile.deployed"
    PAPER_PROFILE_UNDEPLOYED = "paper_profile.undeployed"

    # Paper Profiles
    PAPER_PROFILE_CREATED = "paper_profile.created"
    PAPER_PROFILE_UPDATED = "paper_profile.updated"
    PAPER_PROFILE_DELETED = "paper_profile.deleted"

    # Settings
    SETTINGS_UPDATED = "settings.updated"
    SETTINGS_API_KEY_ROTATED = "settings.api_key_rotated"

    # System
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"
    SYSTEM_BACKUP_CREATED = "system.backup_created"
    SYSTEM_BACKUP_RESTORED = "system.backup_restored"

    # Data Management
    DATA_SYNC_STARTED = "data.sync_started"
    DATA_SYNC_COMPLETED = "data.sync_completed"
    DATA_SYNC_FAILED = "data.sync_failed"

    # Security
    SECURITY_API_KEY_INVALID = "security.api_key_invalid"
    SECURITY_RATE_LIMIT_EXCEEDED = "security.rate_limit_exceeded"
    SECURITY_UNAUTHORIZED_ACCESS = "security.unauthorized_access"


@dataclass
class AuditEvent:
    """Represents a single audit event."""

    event_id: str = field(default_factory=lambda: uuid4().hex[:12])
    timestamp: datetime = field(default_factory=datetime.utcnow)
    event_type: AuditEventType = AuditEventType.SYSTEM_STARTUP
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
    """Thread-safe audit logger with PostgreSQL persistence."""

    def __init__(self):
        self._lock = threading.Lock()
        self._logger = logging.getLogger("audit")
        self._logger.setLevel(logging.INFO)

    async def _ensure_table(self):
        """Create audit log table if it doesn't exist."""
        from src.pg_database import get_pg_manager

        pg_manager = get_pg_manager()
        pool = pg_manager.get_pool("system")

        async with pool.acquire() as conn:
            await conn.execute(
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
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)
            """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)
            """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type)
            """
            )

    async def log(
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

        # Ensure the table exists (fresh DB: nothing has been logged yet)
        await self._ensure_table()

        # Persist to PostgreSQL
        await save_audit_log_pg(event.to_dict())

        # Also log to standard logger
        self._logger.info(
            f"AUDIT: {event.event_type.value} - User: {username} - Action: {action}"
        )

        return event

    async def query(
        self,
        event_type: AuditEventType | None = None,
        user_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query audit logs with optional filters."""
        from src.pg_database import get_pg_manager

        # Ensure the table exists (fresh DB: nothing has been logged yet)
        await self._ensure_table()

        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        param_idx = 1

        if event_type:
            query += f" AND event_type = ${param_idx}"
            params.append(event_type.value)
            param_idx += 1
        if user_id:
            query += f" AND user_id = ${param_idx}"
            params.append(user_id)
            param_idx += 1
        if start_time:
            query += f" AND timestamp >= ${param_idx}"
            params.append(start_time.isoformat())
            param_idx += 1
        if end_time:
            query += f" AND timestamp <= ${param_idx}"
            params.append(end_time.isoformat())
            param_idx += 1

        query += f" ORDER BY timestamp DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([limit, offset])

        async with get_pg_manager().get_pool("system").acquire() as conn:
            rows = await conn.fetch(query, *params)
            result = []
            for r in rows:
                d = dict(r)
                # details is stored as JSON text — parse back to dict (route models
                # expect dict; sqlite-era behaviour returned dicts too)
                if isinstance(d.get("details"), str):
                    try:
                        d["details"] = json.loads(d["details"])
                    except (json.JSONDecodeError, TypeError):
                        d["details"] = {}
                if "success" in d and not isinstance(d["success"], bool):
                    d["success"] = bool(d["success"])
                result.append(d)
            return result

    # ── Convenience helpers (historical AuditLogger API surface) ──

    async def log_login(
        self,
        user_id: str | None = None,
        username: str | None = None,
        ip: str | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> AuditEvent:
        """Log a login attempt (or failure)."""
        return await self.log(
            event_type=(
                AuditEventType.LOGIN if success else AuditEventType.LOGIN_FAILED
            ),
            user_id=user_id,
            username=username,
            ip_address=ip,
            action="login",
            success=success,
            error_message=error,
        )

    async def log_api_key_created(
        self,
        user_id: str | None = None,
        username: str | None = None,
        key_name: str = "",
        key_prefix: str = "",
    ) -> AuditEvent:
        """Log an API key creation."""
        return await self.log(
            event_type=AuditEventType.API_KEY_CREATED,
            user_id=user_id,
            username=username,
            action="api_key_created",
            resource_type="api_key",
            details={"key_name": key_name, "key_prefix": key_prefix},
        )

    async def log_strategy_change(
        self,
        user_id: str | None = None,
        username: str | None = None,
        event_type: AuditEventType = AuditEventType.STRATEGY_UPDATED,
        strategy_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Log a strategy lifecycle change."""
        return await self.log(
            event_type=event_type,
            user_id=user_id,
            username=username,
            resource_id=strategy_id,
            resource_type="strategy",
            action=event_type.value.split(".")[-1],
            details=details or {},
        )

    async def log_paper_trade(
        self,
        user_id: str | None = None,
        username: str | None = None,
        trade_data: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Log a paper trade execution."""
        return await self.log(
            event_type=AuditEventType.PAPER_TRADE_EXECUTED,
            user_id=user_id,
            username=username,
            resource_type="paper_trade",
            action="paper_trade_executed",
            details=trade_data or {},
        )

    async def log_order_execution(
        self,
        user_id: str | None = None,
        username: str | None = None,
        order_data: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Log an order execution."""
        return await self.log(
            event_type=AuditEventType.ORDER_EXECUTED,
            user_id=user_id,
            username=username,
            resource_type="order",
            action="order_executed",
            details=order_data or {},
        )


# Singleton instance for shared state
_audit_logger_instance: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Return the singleton AuditLogger (reuse across calls)."""
    global _audit_logger_instance
    if _audit_logger_instance is None:
        _audit_logger_instance = AuditLogger()
    return _audit_logger_instance


async def log_audit(
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
) -> dict[str, Any]:
    """Global convenience wrapper: log via the singleton AuditLogger."""
    logger = get_audit_logger()
    event = await logger.log(
        event_type=event_type,
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        user_agent=user_agent,
        resource_id=resource_id,
        resource_type=resource_type,
        action=action,
        details=details,
        success=success,
        error_message=error_message,
    )
    return event.to_dict()
