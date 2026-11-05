"""
Centralized Settings and Secrets Management Store.
Manages system configuration, exchange API keys, broker credentials, and risk settings
with automatic secret masking for secure REST API retrieval.
"""

import json
import os
from typing import Any

from src.pg_database import get_setting_pg, load_all_settings_pg, save_setting_pg
from src.sync_bridge import run_async, run_pg

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


# Default canonical symbol mapping (exchange-specific symbols per canonical symbol)
DEFAULT_CANONICAL_SYMBOL_MAPPING: dict[str, dict[str, Any]] = {
    "BTC/USD": {
        "gemini": "BTC/USDT",
        "kucoin": "BTC/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "ETH/USD": {
        "gemini": "ETH/USDT",
        "kucoin": "ETH/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "SOL/USD": {
        "gemini": "SOL/USDT",
        "kucoin": "SOL/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "AVAX/USD": {
        "gemini": "AVAX/USDT",
        "kucoin": "AVAX/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "ADA/USD": {
        "gemini": "ADA/USDT",
        "kucoin": "ADA/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "XRP/USD": {
        "gemini": "XRP/USDT",
        "kucoin": "XRP/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "LTC/USD": {
        "gemini": "LTC/USDT",
        "kucoin": "LTC/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "DOGE/USD": {
        "gemini": "DOGE/USDT",
        "kucoin": "DOGE/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "LINK/USD": {
        "gemini": "LINK/USDT",
        "kucoin": "LINK/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "DOT/USD": {
        "gemini": "DOT/USDT",
        "kucoin": "DOT/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "MATIC/USD": {
        "gemini": "MATIC/USDT",
        "kucoin": "MATIC/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "UNI/USD": {
        "gemini": "UNI/USDT",
        "kucoin": "UNI/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "AAVE/USD": {
        "gemini": "AAVE/USDT",
        "kucoin": "AAVE/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "NEAR/USD": {
        "gemini": "NEAR/USDT",
        "kucoin": "NEAR/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "ATOM/USD": {
        "gemini": "ATOM/USDT",
        "kucoin": "ATOM/USD",
        "prefer_history": "kucoin",
        "prefer_live": "gemini",
    },
    "AAPL": {
        "gemini": "AAPL",
        "synthetic_stock": "AAPL",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
    "TSLA": {
        "gemini": "TSLA",
        "synthetic_stock": "TSLA",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
    "NVDA": {
        "gemini": "NVDA",
        "synthetic_stock": "NVDA",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
    "SPY": {
        "gemini": "SPY",
        "synthetic_stock": "SPY",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
    "QQQ": {
        "gemini": "QQQ",
        "synthetic_stock": "QQQ",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
    "IWM": {
        "gemini": "IWM",
        "synthetic_stock": "IWM",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
    "GLD": {
        "gemini": "GLD",
        "synthetic_stock": "GLD",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
    "SLV": {
        "gemini": "SLV",
        "synthetic_stock": "SLV",
        "prefer_history": "synthetic_stock",
        "prefer_live": "gemini",
    },
}

CANONICAL_SYMBOL_KEY = "canonical_symbol_mapping"

# Sensitive setting keys — API keys and secrets (never stored in localStorage)
SENSITIVE_SETTING_KEYS = {
    "app_api_key",
    "gemini_api_key",
    "gemini_secret_key",
    "kucoin_api_key",
    "kucoin_secret_key",
    "kucoin_passphrase",
    "robinhood_api_key",
    "robinhood_private_key",
    "alpaca_api_key_id",
    "alpaca_secret_key",
    "polygon_api_key",
    "tiingo_api_token",
}


class SettingsManager:
    """Centralized manager for application settings and credentials."""

    def __init__(self, db: Any | None = None) -> None:
        """Create a settings manager.

        ``db`` is accepted for backward compatibility with callers/tests that
        pass a SQLite CandleDatabase; settings now resolve via PG directly.
        """

    def get(self, key: str, default: str | None = None) -> str | None:
        """
        Retrieve setting value by key.
        Checks PostgreSQL database first, then environment variables, then default value.
        Works from sync contexts. For async contexts, use aget().
        """

        setting = run_async(get_setting_pg(key))

        if setting and setting.get("value") is not None:
            return str(setting["value"])

        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val

        return default

    async def aget(self, key: str, default: str | None = None) -> str | None:
        """Async variant of get() - safe from the main event loop."""

        setting = await run_pg(get_setting_pg(key))

        if setting and setting.get("value") is not None:
            return str(setting["value"])

        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val

        return default

    def set(self, key: str, value: str, is_secret: bool | None = None) -> None:
        """Set a setting value in PostgreSQL database."""

        secret_flag = is_secret if is_secret is not None else is_secret_key(key)
        run_async(save_setting_pg(key, str(value), secret_flag))

    async def aset(self, key: str, value: str, is_secret: bool | None = None) -> None:
        """Async variant of set() - safe from the main event loop."""

        secret_flag = is_secret if is_secret is not None else is_secret_key(key)
        await run_pg(save_setting_pg(key, str(value), secret_flag))

    def get_masked(self, key: str) -> str | None:
        """Retrieve setting value with secret masking applied if applicable."""

        setting = run_async(get_setting_pg(key))

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

    async def aget_masked(self, key: str) -> str | None:
        """Async variant of get_masked() - safe from the main event loop."""

        setting = await run_pg(get_setting_pg(key))

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

        settings_rows = run_async(load_all_settings_pg())

        result: dict[str, str] = {}
        for row in settings_rows:
            k = row["key"]
            v = str(row["value"])
            is_sec = bool(row.get("is_secret")) or is_secret_key(k)
            result[k] = mask_secret_value(v) if is_sec else v
        return result

    async def aget_all_masked(self) -> dict[str, str]:
        """Async variant of get_all_masked() - safe from the main event loop."""

        settings_rows = await run_pg(load_all_settings_pg())

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
            self.set(k, val_str, secret_flag)
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

    def get_all_sensitive(self) -> dict[str, str]:
        """Retrieve all sensitive settings (API keys, secrets) for the current session."""
        result = {}
        for key in SENSITIVE_SETTING_KEYS:
            val = self.get(key)
            if val:
                result[key] = val
        return result

    def update_sensitive(self, settings_map: dict[str, Any]) -> int:
        """Update sensitive settings (API keys, secrets) from a dictionary."""
        updated_count = 0
        for k, v in settings_map.items():
            if k in SENSITIVE_SETTING_KEYS and v is not None:
                val_str = str(v).strip()
                if val_str:  # Only update if non-empty
                    run_async(save_setting_pg(k, str(v), True))
                    updated_count += 1
        return updated_count

    async def aget_all_sensitive(self) -> dict[str, str]:
        """Async variant of get_all_sensitive — safe from the main event loop.

        Sync wrappers route through run_async, which detours through a fresh
        loop and crashes against main-loop-bound asyncpg pools when called
        from async routes. This awaits the PG reads directly (run_pg keeps
        foreign-loop callers working too).
        """
        result = {}
        for key in SENSITIVE_SETTING_KEYS:
            row = await run_pg(get_setting_pg(key))
            if row and row.get("value"):
                result[key] = str(row["value"])
        return result

    async def aupdate_sensitive(self, settings_map: dict[str, Any]) -> int:
        """Async variant of update_sensitive — safe from the main event loop."""
        updated_count = 0
        for k, v in settings_map.items():
            if k in SENSITIVE_SETTING_KEYS and v is not None:
                val_str = str(v).strip()
                if val_str:  # Only update if non-empty
                    await run_pg(save_setting_pg(k, str(v), True))
                    updated_count += 1
        return updated_count

    # ============================================================
    # Canonical Symbol Mapping Methods
    # ============================================================

    def get_canonical_symbol_mapping(self) -> dict[str, dict[str, Any]]:
        """Retrieve the canonical symbol mapping from database, falling back to defaults."""
        mapping_str = self.get(CANONICAL_SYMBOL_KEY)
        if mapping_str:
            try:
                return json.loads(mapping_str)
            except json.JSONDecodeError:
                pass
        return DEFAULT_CANONICAL_SYMBOL_MAPPING

    async def aget_canonical_symbol_mapping(self) -> dict[str, dict[str, Any]]:
        """Async variant of get_canonical_symbol_mapping()."""
        mapping_str = await self.aget(CANONICAL_SYMBOL_KEY)
        if mapping_str:
            try:
                return json.loads(mapping_str)
            except json.JSONDecodeError:
                pass
        return DEFAULT_CANONICAL_SYMBOL_MAPPING

    def set_canonical_symbol_mapping(self, mapping: dict[str, dict[str, Any]]) -> None:
        """Save canonical symbol mapping to database."""
        self.set(CANONICAL_SYMBOL_KEY, json.dumps(mapping))

    async def aset_canonical_symbol_mapping(
        self, mapping: dict[str, dict[str, Any]]
    ) -> None:
        """Async variant of set_canonical_symbol_mapping()."""
        await self.aset(CANONICAL_SYMBOL_KEY, json.dumps(mapping))

    def resolve_exchange_symbol(
        self, canonical_symbol: str, live_mode: bool = False
    ) -> tuple[str, str]:
        """
        Resolve a canonical symbol (e.g., 'BTC/USD') to exchange-specific symbol.

        Args:
            canonical_symbol: The canonical symbol (e.g., 'BTC/USD')
            live_mode: If True, use prefer_live exchange; otherwise prefer_history

        Returns:
            Tuple of (exchange, exchange_specific_symbol)
        """
        mapping = self.get_canonical_symbol_mapping()
        if canonical_symbol not in mapping:
            # Not in mapping - return canonical as-is with default exchanges
            if live_mode:
                return "gemini", canonical_symbol
            return "kucoin", canonical_symbol

        symbol_map = mapping[canonical_symbol]
        if live_mode:
            exchange = symbol_map.get("prefer_live", "gemini")
        else:
            exchange = symbol_map.get("prefer_history", "kucoin")

        exchange_symbol = symbol_map.get(exchange, canonical_symbol)
        return exchange, exchange_symbol

    async def aresolve_exchange_symbol(
        self, canonical_symbol: str, live_mode: bool = False
    ) -> tuple[str, str]:
        """
        Async variant of resolve_exchange_symbol() - safe from the main event loop.
        """
        mapping = await self.aget_canonical_symbol_mapping()
        if canonical_symbol not in mapping:
            if live_mode:
                return "gemini", canonical_symbol
            return "kucoin", canonical_symbol

        symbol_map = mapping[canonical_symbol]
        if live_mode:
            exchange = symbol_map.get("prefer_live", "gemini")
        else:
            exchange = symbol_map.get("prefer_history", "kucoin")

        exchange_symbol = symbol_map.get(exchange, canonical_symbol)
        return exchange, exchange_symbol

    def resolve_all_canonical(
        self, live_mode: bool = False
    ) -> dict[str, tuple[str, str]]:
        """
        Resolve all canonical symbols to exchange-specific symbols.

        Returns:
            Dict mapping canonical_symbol -> (exchange, exchange_symbol)
        """
        mapping = self.get_canonical_symbol_mapping()
        result = {}
        for canonical, symbol_map in mapping.items():
            if live_mode:
                exchange = symbol_map.get("prefer_live", "gemini")
            else:
                exchange = symbol_map.get("prefer_history", "kucoin")
            exchange_symbol = symbol_map.get(exchange, canonical)
            result[canonical] = (exchange, exchange_symbol)
        return result

    async def aresolve_all_canonical(
        self, live_mode: bool = False
    ) -> dict[str, tuple[str, str]]:
        """Async variant of resolve_all_canonical()."""
        mapping = await self.aget_canonical_symbol_mapping()
        result = {}
        for canonical, symbol_map in mapping.items():
            if live_mode:
                exchange = symbol_map.get("prefer_live", "gemini")
            else:
                exchange = symbol_map.get("prefer_history", "kucoin")
            exchange_symbol = symbol_map.get(exchange, canonical)
            result[canonical] = (exchange, exchange_symbol)
        return result
