"""
Base Data Provider Abstract Class and Data Ingestion Contracts.
"""

from abc import ABC, abstractmethod
from typing import ClassVar

import pandas as pd

# Ordered standard timeframes from smallest to largest
STANDARD_TF_ORDER: list[str] = [
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "1d",
]


def normalize_symbol(symbol: str) -> str:
    """
    Normalize any symbol variant (btcusd, BTC-USDT, btc/usd, BTC_USD) to canonical 'BASE/QUOTE' format.
    Example: 'BTC/USD' or 'BTC/USDT' or 'AAPL/USD'.
    """
    clean = symbol.strip().upper().replace("-", "/").replace("_", "")
    if "/" not in clean:
        if clean.endswith("USDT"):
            clean = f"{clean[:-4]}/USDT"
        elif clean.endswith("USDC"):
            clean = f"{clean[:-4]}/USDC"
        elif clean.endswith("USD"):
            clean = f"{clean[:-3]}/USD"
        else:
            clean = f"{clean}/USD"
    return clean


class BaseDataProvider(ABC):
    """Abstract base class for all exchange/market data providers."""

    name: ClassVar[str] = "base"
    asset_type: ClassVar[str] = "crypto"  # 'crypto', 'stock', 'synthetic'
    native_timeframes: ClassVar[set[str]] = {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "1d",
    }

    def get_supported_timeframes(self) -> set[str]:
        """Return the set of timeframes natively supported by the provider REST API."""
        return set(self.native_timeframes)

    def get_donor_timeframe(self, target_tf: str) -> str | None:
        """
        Return the largest native timeframe smaller than target_tf that can be resampled up.
        Returns None if target_tf is natively supported or if no donor is available.
        """
        if target_tf in self.native_timeframes:
            return None
        try:
            target_idx = STANDARD_TF_ORDER.index(target_tf)
        except ValueError:
            return None

        for tf in reversed(STANDARD_TF_ORDER[:target_idx]):
            if tf in self.native_timeframes:
                return tf
        return None

    def normalize_symbol(self, symbol: str) -> str:
        """Canonicalize symbol to BASE/QUOTE format."""
        return normalize_symbol(symbol)

    @abstractmethod
    def format_symbol(self, symbol: str) -> str:
        """Format canonical symbol into provider-specific format (e.g. btcusd, BTC-USDT)."""

    @abstractmethod
    def fetch_historical_candles(
        self,
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        days: int = 360,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV candle data from provider.
        Must return DataFrame with normalized UTC 'timestamp', 'open', 'high', 'low', 'close', 'volume'.
        """

    @classmethod
    def validate_and_normalize_df(cls, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate and normalize candle DataFrame:
        - Ensure required OHLCV columns exist
        - Convert timestamp to UTC datetime
        - Convert OHLCV columns to float
        - Deduplicate and sort chronologically
        """
        if df is None or df.empty:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

        df = df.copy()
        required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Missing required candle column: '{col}'")

        # Normalize timestamp to UTC datetime
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            # Check if integer / float unix timestamp
            if pd.api.types.is_numeric_dtype(df["timestamp"]):
                max_val = df["timestamp"].max()
                unit = "ms" if max_val > 1e11 else "s"
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit=unit, utc=True)
            else:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        elif df["timestamp"].dt.tz is None:
            # Localize naive timestamp to UTC
            df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
        else:
            df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        # Sort and deduplicate
        df = (
            df.sort_values("timestamp")
            .drop_duplicates("timestamp")
            .reset_index(drop=True)
        )
        return df[["timestamp", "open", "high", "low", "close", "volume"]]
