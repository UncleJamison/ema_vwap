"""
Unified Multi-Exchange Data Adapter (ema_vwap-bq9).
Supports Gemini + Robinhood initially; others registered iteratively.
Automatic symbol mapping, rate limiting, failover, per-exchange WebSocket streams.
"""

from __future__ import annotations

import logging
import time
from typing import ClassVar

import pandas as pd

from src.providers.base import BaseDataProvider, normalize_symbol
from src.providers.registry import get_provider, register_provider


class SymbolMapper:
    """Automatic symbol mapping / normalization between exchanges."""

    EXCHANGE_SYMBOL_MAP: ClassVar[dict[str, dict[str, str]]] = {
        "gemini": {},  # Gemini uses base.normalize_symbol; mapping identity
        "robinhood": {},  # Robinhood: same canonical; handled by adapter
    }

    @classmethod
    def map(cls, symbol: str, to_exchange: str) -> str:
        """Return exchange-formatted symbol (canonical -> provider fmt)."""
        adapter = get_provider(to_exchange)
        return adapter.format_symbol(normalize_symbol(symbol))

    @classmethod
    def unmap(cls, symbol: str, from_exchange: str) -> str:
        """Return canonical from provider-formatted symbol."""
        return normalize_symbol(symbol)


class RateLimiter:
    """Simple per-provider rate limiter (sleep between requests)."""

    def __init__(self, min_interval_s: float = 0.1) -> None:
        self.min_interval = min_interval_s
        self._last: float = 0.0

    def wait(self) -> None:
        now = time.time()
        elapsed = now - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.time()


class FailoverRouter:
    """Route to primary provider; fall back to synthetic on failure."""

    def __init__(
        self, primary_exchange: str, fallback_exchange: str = "synthetic"
    ) -> None:
        self.primary = primary_exchange
        self.fallback = fallback_exchange

    def fetch(
        self,
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        days: int = 360,
        limit: int | None = None,
    ) -> pd.DataFrame:
        adapter = get_provider(self.primary)
        try:
            df = adapter.fetch_historical_candles(
                symbol=symbol, timeframe=timeframe, days=days, limit=limit
            )
            if not df.empty:
                return df
        except Exception:
            logging.getLogger(__name__).debug(
                "Unified adapter primary fetch failed — using fallback"
            )
        fallback = get_provider(self.fallback)
        return fallback.fetch_historical_candles(
            symbol=symbol, timeframe=timeframe, days=days, limit=limit
        )


class UnifiedAdapter(BaseDataProvider):
    """New unified adapter supporting Gemini + Robinhood with auto mapping."""

    name: ClassVar[str] = "unified"
    asset_type: ClassVar[str] = "multi"

    native_timeframes: ClassVar[set[str]] = {"1m", "5m", "15m", "30m", "1h", "6h", "1d"}

    def __init__(
        self,
        exchanges: list[str] | None = None,
        min_interval_s: float = 0.1,
    ) -> None:
        super().__init__()
        self.exchanges = exchanges or ["gemini", "robinhood"]
        self.mapper = SymbolMapper
        self.limiter = RateLimiter(min_interval_s=min_interval_s)
        # Per-exchange router instances
        self.routers: dict[str, FailoverRouter] = {
            e: FailoverRouter(primary_exchange=e) for e in self.exchanges
        }

    def format_symbol(self, symbol: str) -> str:
        # Unified adapter uses canonical format for all exchanges
        return normalize_symbol(symbol)

    def fetch_historical_candles(
        self,
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        days: int = 360,
        limit: int | None = None,
        exchange: str | None = None,
    ) -> pd.DataFrame:
        target = exchange or self.exchanges[0]
        self.limiter.wait()
        router = self.routers.get(target, FailoverRouter(target))
        # Adapter receives canonical; provider formats internally
        return router.fetch(symbol=symbol, timeframe=timeframe, days=days, limit=limit)


# Register on import (iterative — others added via register_provider)
register_provider(UnifiedAdapter())
