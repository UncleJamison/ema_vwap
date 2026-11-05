"""
User Management & RBAC Models (ema_vwap-108).

Defines User, Role, Permission, and API Key models with SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


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
            self.locked_until and datetime.now(UTC) < self.locked_until
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
        return bool(self.expires_at and datetime.now(UTC) > self.expires_at)

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


# Database operations (PostgreSQL — async methods, awaited directly by deps)
class UserManager:
    """Manages user accounts in the PostgreSQL system database.

    All methods are async: they are called from async FastAPI dependencies
    running on the main event loop, so they must be awaited directly
    (never routed through sync_bridge — that would break the asyncpg pool).
    """

    def __init__(self, db=None):
        # db arg kept for backward compat; PG pool is used internally.
        self._tables_ready = False

    async def _ensure_tables(self):
        """Create user tables if they don't exist (idempotent, lazy)."""
        if self._tables_ready:
            return
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    full_name TEXT DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'viewer',
                    is_active BOOLEAN DEFAULT TRUE,
                    is_superuser BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    last_login TIMESTAMPTZ,
                    failed_login_attempts INTEGER DEFAULT 0,
                    locked_until TIMESTAMPTZ,
                    api_key TEXT UNIQUE NOT NULL,
                    api_key_created_at TIMESTAMPTZ,
                    api_key_last_used TIMESTAMPTZ,
                    current_session_id TEXT
                )
            """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    permissions JSONB DEFAULT '[]',
                    is_active BOOLEAN DEFAULT TRUE,
                    expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL,
                    last_used TIMESTAMPTZ,
                    created_by TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)"
            )
        self._tables_ready = True

    async def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        full_name: str = "",
        role: RoleName = RoleName.VIEWER,
        is_superuser: bool = False,
    ) -> User:
        """Create a new user."""
        await self._ensure_tables()
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            role=role,
            is_superuser=is_superuser,
        )
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, username, email, password_hash, full_name, role,
                                   is_active, is_superuser, created_at, updated_at, api_key)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                user.id,
                user.username,
                user.email,
                user.password_hash,
                user.full_name,
                user.role.value,
                user.is_active,
                user.is_superuser,
                user.created_at,
                user.updated_at,
                user.api_key,
            )
        return user

    async def _fetch_user(self, where: str, arg) -> User | None:
        await self._ensure_tables()
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT * FROM users WHERE {where} = $1", arg)
        if not row:
            return None
        return self._row_to_user(row)

    async def get_user(self, user_id: str) -> User | None:
        """Get user by ID."""
        return await self._fetch_user("id", user_id)

    async def get_user_by_username(self, username: str) -> User | None:
        """Get user by username."""
        return await self._fetch_user("username", username)

    async def get_user_by_email(self, email: str) -> User | None:
        """Get user by email."""
        return await self._fetch_user("email", email)

    async def get_user_by_api_key(self, api_key: str) -> User | None:
        """Get user by API key."""
        return await self._fetch_user("api_key", api_key)

    async def update_user(self, user: User) -> bool:
        """Update user record."""
        await self._ensure_tables()
        user.updated_at = datetime.now(UTC)
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users SET username = $1, email = $2, password_hash = $3,
                               full_name = $4, role = $5, is_active = $6,
                               is_superuser = $7, updated_at = $8,
                               last_login = $9, failed_login_attempts = $10,
                               locked_until = $11, api_key = $12,
                               api_key_created_at = $13, api_key_last_used = $14,
                               current_session_id = $15
                WHERE id = $16
            """,
                user.username,
                user.email,
                user.password_hash,
                user.full_name,
                user.role.value,
                user.is_active,
                user.is_superuser,
                user.updated_at,
                user.last_login,
                user.failed_login_attempts,
                user.locked_until,
                user.api_key,
                user.api_key_created_at,
                user.api_key_last_used,
                user.current_session_id,
                user.id,
            )
        return True

    async def delete_user(self, user_id: str) -> bool:
        """Delete user."""
        await self._ensure_tables()
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM api_keys WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
        return True

    async def list_users(self, active_only: bool = False) -> list[User]:
        """List all users."""
        await self._ensure_tables()
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            if active_only:
                rows = await conn.fetch("SELECT * FROM users WHERE is_active = TRUE")
            else:
                rows = await conn.fetch("SELECT * FROM users")
        return [self._row_to_user(r) for r in rows]

    def _row_to_user(self, row) -> User:
        """Convert a database row (dict-like) to a User object."""
        return User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            password_hash=row["password_hash"],
            full_name=row["full_name"] or "",
            role=RoleName(row["role"]),
            is_active=bool(row["is_active"]),
            is_superuser=bool(row["is_superuser"]),
            created_at=row["created_at"] or datetime.now(UTC),
            updated_at=row["updated_at"] or datetime.now(UTC),
            last_login=row["last_login"],
            failed_login_attempts=row["failed_login_attempts"] or 0,
            locked_until=row["locked_until"],
            api_key=row["api_key"] or "",
            api_key_created_at=row["api_key_created_at"],
            api_key_last_used=row["api_key_last_used"],
            current_session_id=row["current_session_id"],
        )

    async def create_api_key(
        self,
        user_id: str,
        name: str,
        description: str = "",
        permissions: set[PermissionName] | None = None,
        expires_at: datetime | None = None,
        created_by: str = "",
    ) -> APIKey:
        """Create a new API key for a user."""
        import json

        await self._ensure_tables()
        api_key = APIKey(
            user_id=user_id,
            name=name,
            description=description,
            permissions=permissions or set(),
            expires_at=expires_at,
            created_by=created_by,
        )
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO api_keys (key, user_id, name, description, permissions,
                                     is_active, expires_at, created_at, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
                api_key.key,
                api_key.user_id,
                api_key.name,
                api_key.description,
                json.dumps([p.value for p in api_key.permissions]),
                api_key.is_active,
                api_key.expires_at,
                api_key.created_at,
                api_key.created_by,
            )
        return api_key

    async def get_api_key(self, key: str) -> APIKey | None:
        """Get API key by key string."""
        await self._ensure_tables()
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM api_keys WHERE key = $1", key)
        if not row:
            return None
        return self._row_to_api_key(row)

    async def list_api_keys(self, user_id: str) -> list[APIKey]:
        """List API keys for a user."""
        await self._ensure_tables()
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM api_keys WHERE user_id = $1", user_id
            )
        return [self._row_to_api_key(r) for r in rows]

    async def revoke_api_key(self, key: str) -> bool:
        """Revoke an API key."""
        await self._ensure_tables()
        from src.pg_connection_manager import get_pg_manager

        mgr = get_pg_manager()
        pool = mgr.get_pool("system")
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE api_keys SET is_active = FALSE WHERE key = $1", key
            )
        return True

    def _row_to_api_key(self, row) -> APIKey:
        """Convert a database row (dict-like) to an APIKey object."""
        import json

        perms_raw = row["permissions"]
        if isinstance(perms_raw, str):
            try:
                perms_raw = json.loads(perms_raw)
            except (json.JSONDecodeError, TypeError):
                perms_raw = []

        return APIKey(
            key=row["key"],
            user_id=row["user_id"],
            name=row["name"],
            description=row["description"] or "",
            permissions={PermissionName(p) for p in (perms_raw or [])},
            is_active=bool(row["is_active"]),
            expires_at=row["expires_at"],
            created_at=row["created_at"] or datetime.now(UTC),
            last_used=row["last_used"],
            created_by=row["created_by"] or "",
        )
