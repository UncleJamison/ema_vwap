"""
Centralized Settings and Secrets Management Store.
Manages system configuration, exchange API keys, broker credentials, and risk settings
with automatic secret masking for secure REST API retrieval.
"""

import os
from typing import Any

from src.database import CandleDatabase

SECRET_KEYWORDS: set[str] = {
    "secret",
    "key",
    "token",
    "password",
    "passphrase",
    "credential",
    "private",
    "auth",
}


def is_secret_key(key: str) -> bool:
    """Determine if a setting key represents a secret credential based on name conventions."""
    key_lower = key.lower()
    return any(kw in key_lower for kw in SECRET_KEYWORDS)


def mask_secret_value(value: str) -> str:
    """Mask a secret string value so it can be safely exposed to UI/logs."""
    if not value:
        return ""
    val_str = str(value)
    if len(val_str) <= 4:
        return "••••"
    return "••••••••" + val_str[-4:]


class SettingsManager:
    """Centralized manager for application settings and credentials."""

    def __init__(self, db: CandleDatabase | None = None):
        self.db = db or CandleDatabase()

    def get(self, key: str, default: str | None = None) -> str | None:
        """
        Retrieve setting value by key.
        Checks SQLite database first, then environment variables, then default value.
        """
        setting = self.db.get_setting(key)
        if setting and setting.get("value") is not None:
            return str(setting["value"])

        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val

        return default

    def set(self, key: str, value: str, is_secret: bool | None = None) -> None:
        """Set a setting value in SQLite database."""
        secret_flag = is_secret if is_secret is not None else is_secret_key(key)
        self.db.save_setting(key=key, value=str(value), is_secret=secret_flag)

    def get_masked(self, key: str) -> str | None:
        """Retrieve setting value with secret masking applied if applicable."""
        setting = self.db.get_setting(key)
        if not setting:
            env_val = os.environ.get(key)
            if env_val is None:
                return None
            if is_secret_key(key):
                return mask_secret_value(env_val)
            return env_val

        val = str(setting["value"])
        if setting.get("is_secret") or is_secret_key(key):
            return mask_secret_value(val)
        return val

    def get_all_masked(self) -> dict[str, str]:
        """Return dictionary of all configured settings with secrets safely masked."""
        settings_rows = self.db.load_all_settings()
        result: dict[str, str] = {}
        for row in settings_rows:
            k = row["key"]
            v = str(row["value"])
            is_sec = bool(row.get("is_secret")) or is_secret_key(k)
            result[k] = mask_secret_value(v) if is_sec else v
        return result

    def update_bulk(self, settings_map: dict[str, Any]) -> int:
        """
        Bulk update settings from a dictionary.
        Ignores masked dummy inputs (e.g., '••••••••1234') unless raw value provided.
        """
        updated_count = 0
        for k, v in settings_map.items():
            if v is None:
                continue
            val_str = str(v).strip()
            # Skip if user submitted back the masked placeholder
            if val_str.startswith("••••"):
                continue
            secret_flag = is_secret_key(k)
            self.db.save_setting(key=k, value=val_str, is_secret=secret_flag)
            updated_count += 1
        return updated_count

    def get_alpaca_credentials(self) -> dict[str, Any]:
        """Retrieve Alpaca API credentials and paper endpoint toggle."""
        return {
            "api_key": self.get("alpaca_api_key_id")
            or os.environ.get("APCA_API_KEY_ID", ""),
            "secret_key": self.get("alpaca_secret_key")
            or os.environ.get("APCA_API_SECRET_KEY", ""),
            "is_paper": (
                self.get("alpaca_paper", "true").lower() in ("true", "1", "yes")
            ),
        }

    def get_polygon_api_key(self) -> str:
        """Retrieve Polygon.io API key."""
        return self.get("polygon_api_key") or os.environ.get("POLYGON_API_KEY", "")

    def get_tiingo_api_token(self) -> str:
        """Retrieve Tiingo API token."""
        return self.get("tiingo_api_token") or os.environ.get("TIINGO_API_TOKEN", "")

    def get_gemini_credentials(self) -> dict[str, Any]:
        """Retrieve Gemini Exchange API credentials and sandbox toggle."""
        return {
            "api_key": self.get("gemini_api_key")
            or os.environ.get("GEMINI_API_KEY", ""),
            "secret_key": self.get("gemini_secret_key")
            or os.environ.get("GEMINI_SECRET_KEY", ""),
            "is_sandbox": (
                self.get("gemini_sandbox", "true").lower() in ("true", "1", "yes")
            ),
        }

    def get_kucoin_credentials(self) -> dict[str, Any]:
        """Retrieve KuCoin Exchange API credentials, passphrase, and sandbox toggle."""
        return {
            "api_key": self.get("kucoin_api_key")
            or os.environ.get("KUCOIN_API_KEY", ""),
            "secret_key": self.get("kucoin_secret_key")
            or os.environ.get("KUCOIN_SECRET_KEY", ""),
            "passphrase": self.get("kucoin_passphrase")
            or os.environ.get("KUCOIN_PASSPHRASE", ""),
            "is_sandbox": (
                self.get("kucoin_sandbox", "false").lower() in ("true", "1", "yes")
            ),
        }

    def get_robinhood_credentials(self) -> dict[str, Any]:
        """Retrieve Robinhood Crypto API credentials and Ed25519 private key."""
        return {
            "api_key": self.get("robinhood_api_key")
            or os.environ.get("ROBINHOOD_API_KEY", ""),
            "private_key": self.get("robinhood_private_key")
            or os.environ.get("ROBINHOOD_PRIVATE_KEY", ""),
            "account_number": self.get("robinhood_account_number")
            or os.environ.get("ROBINHOOD_ACCOUNT_NUMBER", ""),
        }

    def get_monte_carlo_ruin_pct(self) -> float:
        """Maximum acceptable probability of 50% account drawdown.

        Reads from settings: max_monte_carlo_ruin_pct (default 5.0 if not set).
        """
        return float(self.get("max_monte_carlo_ruin_pct") or 5.0)

    def get_monte_carlo_drawdown_pct(self) -> float:
        """Maximum acceptable 95th percentile simulated drawdown.

        Reads from settings: max_monte_carlo_drawdown_pct (default 35.0 if not set).
        """
        return float(self.get("max_monte_carlo_drawdown_pct") or 35.0)
