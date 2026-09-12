"""
Crypto Exchange Data Adapters for Gemini and KuCoin.
"""

import time
from typing import ClassVar

import pandas as pd
import requests

from src.providers.base import BaseDataProvider, normalize_symbol


class GeminiAdapter(BaseDataProvider):
    """Data provider adapter for Gemini REST API."""

    name: ClassVar[str] = "gemini"
    asset_type: ClassVar[str] = "crypto"
    native_timeframes: ClassVar[set[str]] = {"1m", "5m", "15m", "30m", "1h", "6h", "1d"}

    def format_symbol(self, symbol: str) -> str:
        """Convert standard symbol format (e.g. BTC/USD or BTC/USDT) to Gemini format (btcusd)."""
        canon = normalize_symbol(symbol)
        clean = canon.replace("/", "").lower()
        if clean.endswith("usdt"):
            clean = clean[:-4] + "usd"
        return clean

    def fetch_historical_candles(
        self,
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        days: int = 180,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Fetch historical candles from Gemini REST API."""
        gemini_symbol = self.format_symbol(symbol)
        gemini_tf_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1hr",
            "6h": "6hr",
            "1d": "1day",
        }
        g_tf = gemini_tf_map.get(timeframe, "5m")
        url = f"https://api.gemini.com/v2/candles/{gemini_symbol}/{g_tf}"

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            raw_data = resp.json()

            if not isinstance(raw_data, list) or len(raw_data) == 0:
                raise ValueError("Empty response from Gemini")

            df = pd.DataFrame(
                raw_data,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            # Gemini timestamps are in milliseconds
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = self.validate_and_normalize_df(df)
            if limit and len(df) > limit:
                df = df.tail(limit).reset_index(drop=True)
            return df
        except Exception as e:
            print(f"[GeminiAdapter] Request error ({e}).")
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )


class KuCoinAdapter(BaseDataProvider):
    """Data provider adapter for KuCoin REST API with pagination."""

    name: ClassVar[str] = "kucoin"
    asset_type: ClassVar[str] = "crypto"
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

    def format_symbol(self, symbol: str) -> str:
        """Convert standard symbol format (e.g. BTC/USD or BTC/USDT) to KuCoin format (BTC-USDT)."""
        canon = normalize_symbol(symbol)
        parts = canon.split("/")
        base = parts[0]
        quote = parts[1]
        if quote == "USD":
            quote = "USDT"
        return f"{base}-{quote}"

    def fetch_historical_candles(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "5m",
        days: int = 180,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """
        Fetch up to `days` of historical intraday candles from KuCoin using paginated requests.
        KuCoin API returns up to 1,500 candles per call in reverse chronological order.
        """
        kucoin_symbol = self.format_symbol(symbol)
        tf_map = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1hour",
            "2h": "2hour",
            "4h": "4hour",
            "6h": "6hour",
            "1d": "1day",
        }
        kc_tf = tf_map.get(timeframe, "5min")

        tf_seconds_map = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "6h": 21600,
            "1d": 86400,
        }
        step_seconds = tf_seconds_map.get(timeframe, 300)

        end_time = int(time.time())
        start_target_time = end_time - (days * 86400)

        all_candles = []
        current_end = end_time

        print(
            f"[KuCoinAdapter] Fetching ~{days} days of historical candles for {symbol} ({timeframe})..."
        )

        while current_end > start_target_time:
            current_start = max(start_target_time, current_end - (1450 * step_seconds))
            url = (
                f"https://api.kucoin.com/api/v1/market/candles?"
                f"symbol={kucoin_symbol}&type={kc_tf}&startAt={current_start}&endAt={current_end}"
            )

            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                res_json = resp.json()

                if res_json.get("code") != "200000" or "data" not in res_json:
                    break

                chunk = res_json["data"]
                if not chunk:
                    break

                all_candles.extend(chunk)
                current_end = current_start - 1
                time.sleep(0.1)  # Respect API rate limits
            except Exception as e:
                print(f"[KuCoinAdapter] Pagination error: {e}")
                break

        if not all_candles:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

        # KuCoin candle format: [time, open, close, high, low, volume, turnover]
        df = pd.DataFrame(
            all_candles,
            columns=["timestamp", "open", "close", "high", "low", "volume", "turnover"],
        )
        df["timestamp"] = pd.to_datetime(
            df["timestamp"].astype(int), unit="s", utc=True
        )
        df = self.validate_and_normalize_df(df)

        if limit and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)

        return df
