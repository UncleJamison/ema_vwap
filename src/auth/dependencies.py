"""
Authentication Dependencies (ema_vwap-108).

FastAPI dependency injectors for authentication and authorization.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from src.auth.models import (
    PermissionName,
    RoleName,
    User,
    UserManager,
)

# API Key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
http_bearer = HTTPBearer(auto_error=False)


# Global user manager instance
_user_manager: UserManager | None = None


def get_user_manager() -> UserManager:
    """Get the global UserManager instance."""
    global _user_manager
    if _user_manager is None:
        _user_manager = UserManager()
    return _user_manager


async def get_current_user(
    request: Request,
    api_key: str | None = Depends(api_key_header),
    auth_header: HTTPAuthorizationCredentials | None = Depends(http_bearer),
    user_manager: UserManager = Depends(get_user_manager),
) -> User:
    """
    Get the current authenticated user.

    Supports both API Key (X-API-Key header) and Bearer token authentication.
    """
    credentials = None

    # Try API Key first
    if api_key:
        credentials = api_key
    elif auth_header and auth_header.scheme.lower() == "bearer":
        credentials = auth_header.credentials

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Provide X-API-Key header or Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Look up user by API key in the users table (RBAC path)
    user = await user_manager.get_user_by_api_key(credentials)
    if not user:
        # Fallback: the administrator API key (stored in settings, not users)
        # authenticates as a synthetic superuser so RBAC-protected routes
        # work out of the box without creating user records.
        from src.auth.security import get_configured_api_key_async

        configured = await get_configured_api_key_async()
        if configured and credentials == configured:
            user = User(
                username="admin",
                email="admin@local",
                role=RoleName.ADMIN,
                is_superuser=True,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key or token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    # Check if user is locked
    if user.is_locked():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account temporarily locked due to failed login attempts",
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current user and ensure they are active."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return current_user


async def get_current_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current user and ensure they are a superuser."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )
    return current_user


def require_permission(permission: PermissionName):
    """
    Dependency factory for permission-based access control.

    Usage:
        @router.get("/endpoint", dependencies=[Depends(require_permission(PermissionName.STRATEGY_READ))])
    """

    async def permission_checker(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if not current_user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission.value} required",
            )
        return current_user

    return permission_checker


def require_role(role: RoleName):
    """
    Dependency factory for role-based access control.

    Usage:
        @router.get("/admin", dependencies=[Depends(require_role(RoleName.ADMIN))])
    """

    async def role_checker(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.role != role and current_user.role != RoleName.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {role.value} required",
            )
        return current_user

    return role_checker


# Convenience dependencies for common permissions
require_admin = require_role(RoleName.ADMIN)
require_researcher = require_role(RoleName.RESEARCHER)
require_trader = require_role(RoleName.TRADER)

# Strategy permissions
require_strategy_read = require_permission(PermissionName.STRATEGY_READ)
require_strategy_write = require_permission(PermissionName.STRATEGY_CREATE)

# Backtest permissions
require_backtest_read = require_permission(PermissionName.BACKTEST_READ)
require_backtest_run = require_permission(PermissionName.BACKTEST_RUN)

# Optimization permissions
require_optimize_read = require_permission(PermissionName.OPTIMIZE_READ)
require_optimize_run = require_permission(PermissionName.OPTIMIZE_RUN)

# Paper trading permissions
require_paper_read = require_permission(PermissionName.PAPER_PROFILE_READ)
require_paper_write = require_permission(PermissionName.PAPER_PROFILE_CREATE)
require_paper_engine = require_permission(PermissionName.PAPER_ENGINE_CONTROL)
require_paper_trade = require_permission(PermissionName.PAPER_TRADE_EXECUTE)

# Portfolio permissions
require_portfolio_read = require_permission(PermissionName.PORTFOLIO_READ)
require_portfolio_write = require_permission(PermissionName.PORTFOLIO_UPDATE)
require_portfolio_rebalance = require_permission(PermissionName.PORTFOLIO_REBALANCE)
require_portfolio_route = require_permission(PermissionName.PORTFOLIO_ROUTE_ORDER)

# Settings permissions
require_settings_read = require_permission(PermissionName.SETTINGS_READ)
require_settings_write = require_permission(PermissionName.SETTINGS_UPDATE)

# Audit permissions
require_audit_read = require_permission(PermissionName.AUDIT_READ)
