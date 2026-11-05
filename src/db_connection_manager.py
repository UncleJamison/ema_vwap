"""
PostgreSQL Connection Manager — Phase 5 of ema_vwap-0n8p.
Provides PostgreSQL connection pools for all domains.
SQLite support completely removed.
"""

from pathlib import Path
from typing import Any

try:
    import asyncpg

    PG_AVAILABLE = True
except ImportError:
    PG_AVAILABLE = False
    asyncpg = None

from src.pg_connection_manager import get_pg_manager

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Domain to PostgreSQL pool mapping (no more SQLite)
DOMAIN_POOLS: dict[str, str] = {
    "candles": "candles",
    "batch_results": "batch_results",
    "paper_trading": "paper_trading",
    "system": "system",
}


def get_pg_pool(db_key: str):
    """Return an asyncpg Pool for the named PostgreSQL domain."""
    if not PG_AVAILABLE:
        raise RuntimeError("asyncpg not installed. Install with: pip install asyncpg")
    pg_manager = get_pg_manager()
    domain = DOMAIN_POOLS.get(db_key)
    if not domain:
        raise KeyError(f"Unknown db_key: {db_key}. Available: {list(DOMAIN_POOLS)}")
    return pg_manager.get_pool(domain)


def get_pg_manager_instance():
    """Return the PostgresConnectionManager instance."""
    from src.pg_connection_manager import get_pg_manager

    return get_pg_manager()


def get_database(db_key: str):
    """
    Deprecated: Returns a PG-aware database interface.
    Use src.pg_database.{function}_pg() functions directly instead.
    """
    domain = DOMAIN_POOLS.get(db_key)
    if not domain:
        raise KeyError(f"Unknown db_key: {db_key}. Available: {list(DOMAIN_POOLS)}")

    # Return a dummy object that raises helpful errors if misused
    class PGDatabaseProxy:
        def __getattr__(self, name):
            raise AttributeError(
                f"'{db_key}' domain uses PostgreSQL. "
                f"Use src.pg_database.{{name}}_pg() functions directly."
            )

    return PGDatabaseProxy()


def get_db_info() -> dict[str, Any]:
    """Return info about PostgreSQL domains (no SQLite)."""
    info: dict[str, Any] = {}
    for key, domain in DOMAIN_POOLS.items():
        info[key] = {
            "domain": domain,
            "type": "postgresql",
            "pool_available": PG_AVAILABLE,
        }
    return info
