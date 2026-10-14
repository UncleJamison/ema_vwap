"""
Security and authentication utilities for API key management and validation.
"""

import os
import secrets

from fastapi import Header, HTTPException, status
from fastapi.security import APIKeyHeader

from src.logger import logger


def get_configured_api_key() -> str | None:
    """
    Retrieve the configured API key from the environment or settings database.
    Fails closed: returns None if the key is empty or set to insecure default.
    """
    # First check environment variable (for Docker/container deployments)
    key = os.environ.get("API_KEY")
    if key and key.strip() and key.strip() != "default-key-for-dev":
        return key.strip()

    # If env var is set to default-key-for-dev, explicitly reject it
    env_key = os.environ.get("API_KEY")
    if env_key and env_key.strip() == "default-key-for-dev":
        return None

    # Fall back to settings database (for persistent config via UI)
    try:
        from src.db_connection_manager import get_database
        from src.settings import SettingsManager

        db = get_database("system")
        mgr = SettingsManager(db)
        key = mgr.get("app_api_key")
        if key and key.strip() and key.strip() != "default-key-for-dev":
            return key.strip()
    except Exception as e:
        logger.debug(f"Failed to load API key from settings: {e}")

    return None


def verify_api_key(provided_key: str | None) -> bool:
    """
    Timing-attack safe comparison between the provided API key and configured API key.
    Always executes in constant time to prevent timing side-channel attacks.
    """
    configured_key = get_configured_api_key()
    if not configured_key or not provided_key:
        return False
    return secrets.compare_digest(provided_key, configured_key)


# FastAPI dependency for API key authentication
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_api_key(api_key: str | None = Header(None, alias="X-API-Key")) -> str:
    """
    FastAPI dependency to extract and verify API key from X-API-Key header.

    Returns the verified API key string, or raises 401/403 on failure.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )

    if not verify_api_key(api_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )

    return api_key
