"""
User Management & RBAC Models (ema_vwap-108).

Defines User, Role, Permission, and API Key models with SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from src.database import CandleDatabase


class RoleName(str, Enum):
    """Pre-defined system roles."""

    ADMIN = "admin"
    RESEARCHER = "researcher"
    TRADER = "trader"
    VIEWER = "viewer"


class PermissionName(str, Enum):
    """Granular permissions."""

    # User management
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"

    # Strategy & Backtest
    STRATEGY_CREATE = "strategy:create"
    STRATEGY_READ = "strategy:read"
    STRATEGY_UPDATE = "strategy:update"
    STRATEGY_DELETE = "strategy:delete"
    BACKTEST_RUN = "backtest:run"
    BACKTEST_READ = "backtest:read"

    # Optimization
    OPTIMIZE_RUN = "optimize:run"
    OPTIMIZE_READ = "optimize:read"
    BATCH_OPTIMIZE_RUN = "batch_optimize:run"
    BATCH_OPTIMIZE_READ = "batch_optimize:read"

    # Paper Trading
    PAPER_PROFILE_CREATE = "paper:profile_create"
    PAPER_PROFILE_READ = "paper:profile_read"
    PAPER_PROFILE_UPDATE = "paper:profile_update"
    PAPER_PROFILE_DELETE = "paper:profile_delete"
    PAPER_ENGINE_CONTROL = "paper:engine_control"
    PAPER_TRADE_EXECUTE = "paper:trade_execute"

    # Portfolio
    PORTFOLIO_READ = "portfolio:read"
    PORTFOLIO_UPDATE = "portfolio:update"
    PORTFOLIO_REBALANCE = "portfolio:rebalance"
    PORTFOLIO_ROUTE_ORDER = "portfolio:route_order"

    # Settings & System
    SETTINGS_READ = "settings:read"
    SETTINGS_UPDATE = "settings:update"
    SYSTEM_DB_MANAGE = "system:db_manage"
    SYSTEM_CACHE_CLEAR = "system:cache_clear"

    # Audit
    AUDIT_READ = "audit:read"


# Role-Permission mapping (default)
ROLE_PERMISSIONS: dict[RoleName, set[PermissionName]] = {
    RoleName.ADMIN: set(PermissionName),  # All permissions
    RoleName.RESEARCHER: {
        PermissionName.USER_READ,
        PermissionName.STRATEGY_CREATE,
        PermissionName.STRATEGY_READ,
        PermissionName.STRATEGY_UPDATE,
        PermissionName.BACKTEST_RUN,
        PermissionName.BACKTEST_READ,
        PermissionName.OPTIMIZE_RUN,
        PermissionName.OPTIMIZE_READ,
        PermissionName.BATCH_OPTIMIZE_RUN,
        PermissionName.BATCH_OPTIMIZE_READ,
        PermissionName.PAPER_PROFILE_CREATE,
        PermissionName.PAPER_PROFILE_READ,
        PermissionName.PAPER_PROFILE_UPDATE,
        PermissionName.PORTFOLIO_READ,
        PermissionName.SETTINGS_READ,
    },
    RoleName.TRADER: {
        PermissionName.USER_READ,
        PermissionName.STRATEGY_READ,
        PermissionName.BACKTEST_READ,
        PermissionName.PAPER_PROFILE_READ,
        PermissionName.PAPER_PROFILE_UPDATE,
        PermissionName.PAPER_ENGINE_CONTROL,
        PermissionName.PAPER_TRADE_EXECUTE,
        PermissionName.PORTFOLIO_READ,
        PermissionName.PORTFOLIO_UPDATE,
        PermissionName.PORTFOLIO_REBALANCE,
        PermissionName.PORTFOLIO_ROUTE_ORDER,
    },
    RoleName.VIEWER: {
        PermissionName.USER_READ,
        PermissionName.STRATEGY_READ,
        PermissionName.BACKTEST_READ,
        PermissionName.OPTIMIZE_READ,
        PermissionName.BATCH_OPTIMIZE_READ,
        PermissionName.PAPER_PROFILE_READ,
        PermissionName.PORTFOLIO_READ,
        PermissionName.SETTINGS_READ,
    },
}


@dataclass
class User:
    """User account model."""

    id: str = field(default_factory=lambda: str(uuid4()))
    username: str = ""
    email: str = ""
    password_hash: str = ""
    full_name: str = ""
    role: RoleName = RoleName.VIEWER
    is_active: bool = True
    is_superuser: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    last_login: datetime | None = None
    failed_login_attempts: int = 0
    locked_until: datetime | None = None

    # API Key for programmatic access
    api_key: str = field(default_factory=lambda: f"ema_{uuid4().hex[:16]}")
    api_key_created_at: datetime | None = None
    api_key_last_used: datetime | None = None

    # Session
    current_session_id: str | None = None

    def to_dict(self, include_sensitive: bool = False) -> dict:
        data = {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role.value,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }
        if include_sensitive:
            data["api_key"] = self.api_key
            data["api_key_created_at"] = (
                self.api_key_created_at.isoformat() if self.api_key_created_at else None
            )
            data["api_key_last_used"] = (
                self.api_key_last_used.isoformat() if self.api_key_last_used else None
            )
        return data

    def has_permission(self, perm: PermissionName) -> bool:
        if self.is_superuser:
            return True
        role_perms = ROLE_PERMISSIONS.get(self.role, set())
        return perm in role_perms

    def is_locked(self) -> bool:
        return bool(
            self.locked_until and datetime.now(timezone.utc) < self.locked_until
        )


@dataclass
class Role:
    """Role definition."""

    name: RoleName
    description: str = ""
    permissions: set[PermissionName] = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class APIKey:
    """API Key for programmatic access."""

    key: str = field(default_factory=lambda: f"ema_{uuid4().hex[:24]}")
    user_id: str = ""
    name: str = ""
    description: str = ""
    permissions: set[PermissionName] = field(default_factory=set)
    is_active: bool = True
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used: datetime | None = None
    created_by: str = ""

    def is_expired(self) -> bool:
        return bool(self.expires_at and datetime.now(timezone.utc) > self.expires_at)

    def to_dict(self, include_key: bool = False) -> dict:
        data = {
            "name": self.name,
            "description": self.description,
            "permissions": [p.value for p in self.permissions],
            "is_active": self.is_active,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used": self.last_used.isoformat() if self.last_used else None,
        }
        if include_key:
            data["key"] = self.key
        return data


# Database operations
class UserManager:
    """Manages user accounts in the system database."""

    def __init__(self, db: CandleDatabase):
        self.db = db
        self._ensure_tables()

    def _ensure_tables(self):
        """Create user tables if they don't exist."""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    full_name TEXT DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'viewer',
                    is_active INTEGER DEFAULT 1,
                    is_superuser INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login TEXT,
                    failed_login_attempts INTEGER DEFAULT 0,
                    locked_until TEXT,
                    api_key TEXT UNIQUE NOT NULL,
                    api_key_created_at TEXT,
                    api_key_last_used TEXT,
                    current_session_id TEXT
                )
            """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    permissions TEXT DEFAULT '[]',
                    is_active INTEGER DEFAULT 1,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    last_used TEXT,
                    created_by TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)
            """
            )
            conn.commit()

    def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        full_name: str = "",
        role: RoleName = RoleName.VIEWER,
        is_superuser: bool = False,
    ) -> User:
        """Create a new user."""
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            role=role,
            is_superuser=is_superuser,
        )

        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (id, username, email, password_hash, full_name, role,
                                 is_active, is_superuser, created_at, updated_at,
                                 api_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    user.id,
                    user.username,
                    user.email,
                    user.password_hash,
                    user.full_name,
                    user.role.value,
                    int(user.is_active),
                    int(user.is_superuser),
                    user.created_at.isoformat(),
                    user.updated_at.isoformat(),
                    user.api_key,
                ),
            )
            conn.commit()

        return user

    def get_user(self, user_id: str) -> User | None:
        """Get user by ID."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_user(row)

    def get_user_by_username(self, username: str) -> User | None:
        """Get user by username."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> User | None:
        """Get user by email."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_user(row)

    def get_user_by_api_key(self, api_key: str) -> User | None:
        """Get user by API key."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE api_key = ?", (api_key,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_user(row)

    def update_user(self, user: User) -> bool:
        """Update user record."""
        user.updated_at = datetime.now(timezone.utc)
        with self.db.get_connection() as conn:
            conn.execute(
                """
                UPDATE users SET username = ?, email = ?, password_hash = ?,
                               full_name = ?, role = ?, is_active = ?,
                               is_superuser = ?, updated_at = ?,
                               last_login = ?, failed_login_attempts = ?,
                               locked_until = ?, api_key = ?,
                               api_key_created_at = ?, api_key_last_used = ?,
                               current_session_id = ?
                WHERE id = ?
            """,
                (
                    user.username,
                    user.email,
                    user.password_hash,
                    user.full_name,
                    user.role.value,
                    int(user.is_active),
                    int(user.is_superuser),
                    user.updated_at.isoformat(),
                    user.last_login.isoformat() if user.last_login else None,
                    user.failed_login_attempts,
                    user.locked_until.isoformat() if user.locked_until else None,
                    user.api_key,
                    (
                        user.api_key_created_at.isoformat()
                        if user.api_key_created_at
                        else None
                    ),
                    (
                        user.api_key_last_used.isoformat()
                        if user.api_key_last_used
                        else None
                    ),
                    user.current_session_id,
                    user.id,
                ),
            )
            conn.commit()
        return True

    def delete_user(self, user_id: str) -> bool:
        """Delete user."""
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.execute("DELETE FROM api_keys WHERE user_id = ?", (user_id,))
            conn.commit()
        return True

    def list_users(self, active_only: bool = False) -> list[User]:
        """List all users."""
        with self.db.get_connection() as conn:
            query = "SELECT * FROM users"
            if active_only:
                query += " WHERE is_active = 1"
            rows = conn.execute(query).fetchall()
            return [self._row_to_user(row) for row in rows]

    def _row_to_user(self, row) -> User:
        """Convert database row to User object."""
        return User(
            id=row[0],
            username=row[1],
            email=row[2],
            password_hash=row[3],
            full_name=row[3] if len(row) > 3 else "",  # Handle schema
            role=RoleName(row[4]),
            is_active=bool(row[5]),
            is_superuser=bool(row[6]),
            created_at=(
                datetime.fromisoformat(row[7]) if row[7] else datetime.now(timezone.utc)
            ),
            updated_at=(
                datetime.fromisoformat(row[8]) if row[8] else datetime.now(timezone.utc)
            ),
            last_login=datetime.fromisoformat(row[9]) if row[9] else None,
            failed_login_attempts=row[10] if len(row) > 10 else 0,
            locked_until=datetime.fromisoformat(row[11]) if row[11] else None,
            api_key=row[12] if len(row) > 12 else "",
            api_key_created_at=(
                datetime.fromisoformat(row[13]) if len(row) > 13 and row[13] else None
            ),
            api_key_last_used=(
                datetime.fromisoformat(row[14]) if len(row) > 14 and row[14] else None
            ),
            current_session_id=row[15] if len(row) > 15 else None,
        )

    def create_api_key(
        self,
        user_id: str,
        name: str,
        description: str = "",
        permissions: set[PermissionName] | None = None,
        expires_at: datetime | None = None,
        created_by: str = "",
    ) -> APIKey:
        """Create a new API key for a user."""
        api_key = APIKey(
            user_id=user_id,
            name=name,
            description=description,
            permissions=permissions or set(),
            expires_at=expires_at,
            created_by=created_by,
        )

        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO api_keys (key, user_id, name, description, permissions,
                                     is_active, expires_at, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    api_key.key,
                    api_key.user_id,
                    api_key.name,
                    api_key.description,
                    "[" + ",".join(f'"{p.value}"' for p in api_key.permissions) + "]",
                    int(api_key.is_active),
                    api_key.expires_at.isoformat() if api_key.expires_at else None,
                    api_key.created_at.isoformat(),
                    api_key.created_by,
                ),
            )
            conn.commit()

        return api_key

    def get_api_key(self, key: str) -> APIKey | None:
        """Get API key by key string."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_api_key(row)

    def list_api_keys(self, user_id: str) -> list[APIKey]:
        """List API keys for a user."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id = ?", (user_id,)
            ).fetchall()
            return [self._row_to_api_key(row) for row in rows]

    def revoke_api_key(self, key: str) -> bool:
        """Revoke an API key."""
        with self.db.get_connection() as conn:
            conn.execute("UPDATE api_keys SET is_active = 0 WHERE key = ?", (key,))
            conn.commit()
        return True

    def _row_to_api_key(self, row) -> APIKey:
        import json

        return APIKey(
            key=row[0],
            user_id=row[1],
            name=row[2],
            description=row[3] or "",
            permissions={PermissionName(p) for p in json.loads(row[4])},
            is_active=bool(row[5]),
            expires_at=datetime.fromisoformat(row[6]) if row[6] else None,
            created_at=(
                datetime.fromisoformat(row[7]) if row[7] else datetime.now(timezone.utc)
            ),
            last_used=datetime.fromisoformat(row[8]) if row[8] else None,
            created_by=row[9] or "",
        )
