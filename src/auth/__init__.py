"""
Authentication & Authorization Module (ema_vwap-108).

Provides:
- User management with roles and permissions
- JWT/API Key authentication
- RBAC dependencies for FastAPI
- Session management
"""

from .dependencies import (
    get_current_active_user,
    get_current_superuser,
    get_current_user,
    require_admin,
    # Audit
    require_audit_read,
    # Backtest
    require_backtest_read,
    require_backtest_run,
    # Optimization
    require_optimize_read,
    require_optimize_run,
    require_paper_engine,
    # Paper Trading
    require_paper_read,
    require_paper_trade,
    require_paper_write,
    require_permission,
    # Portfolio
    require_portfolio_read,
    require_portfolio_rebalance,
    require_portfolio_route,
    require_portfolio_write,
    require_researcher,
    require_role,
    # Settings
    require_settings_read,
    require_settings_write,
    # Strategy
    require_strategy_read,
    require_strategy_write,
    require_trader,
)
from .models import (
    ROLE_PERMISSIONS,
    APIKey,
    PermissionName,
    Role,
    RoleName,
    User,
    UserManager,
)
from .security import (
    get_api_key,
    get_configured_api_key,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "APIKey",
    "PermissionName",
    "Role",
    "RoleName",
    "User",
    "UserManager",
    "get_api_key",
    "get_configured_api_key",
    "get_current_active_user",
    "get_current_superuser",
    "get_current_user",
    "require_admin",
    "require_audit_read",
    "require_backtest_read",
    "require_backtest_run",
    "require_optimize_read",
    "require_optimize_run",
    "require_paper_engine",
    "require_paper_read",
    "require_paper_trade",
    "require_paper_write",
    "require_permission",
    "require_portfolio_read",
    "require_portfolio_rebalance",
    "require_portfolio_route",
    "require_portfolio_write",
    "require_researcher",
    "require_role",
    "require_settings_read",
    "require_settings_write",
    "require_strategy_read",
    "require_strategy_write",
    "require_trader",
]
