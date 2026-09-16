"""
Alpaca Markets Stock Data Provider Adapter.
Implements Alpaca Market Data API v2 with automatic pagination, rate-limit backoff,
and split-adjustment options.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import ClassVar

import pandas as pd

from src.providers.base import BaseDataProvider
from src.settings import SettingsManager

logger = logging.getLogger(__name__)

# Alpaca timeframe mappings to API format
ALPACA_TIMEFRAMES: dict[str, str] = {
    "1m": "1Min",
    "3m": "3Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h": "1Hour",
    "2h": "2Hour",
    "4h": "4Hour",
    "1d": "1Day",
}


class AlpacaStockAdapter(BaseDataProvider):
    """
    Data adapter for Alpaca Markets Stock API v2.
    Retrieves historical OHLCV equity bars with pagination and split adjustments.
    """

    name: ClassVar[str] = "alpaca"
    asset_type: ClassVar[str] = "stock"
    native_timeframes: ClassVar[set[str]] = {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "1d",
    }
    base_url: ClassVar[str] = "https://data.alpaca.markets/v2/stocks"

    def __init__(self, settings_manager: SettingsManager | None = None):
        self.settings = settings_manager or SettingsManager()

    def format_symbol(self, symbol: str) -> str:
        """Strip quote currency and special characters (e.g., 'AAPL/USD' -> 'AAPL')."""
        clean = symbol.strip().upper().replace("-", "/").replace("_", "")
        if "/" in clean:
            return clean.split("/")[0]
        return clean

    def _get_headers(self) -> dict[str, str]:
        """Build Alpaca authentication headers from Settings."""
        creds = self.settings.get_alpaca_credentials()
        api_key = creds.get("api_key") or ""
        secret_key = creds.get("secret_key") or ""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "EMA-VWAP-Stock-Engine/1.0",
        }
        if api_key and secret_key:
            headers["APCA-API-KEY-ID"] = api_key
            headers["APCA-API-SECRET-KEY"] = secret_key
        return headers

    def fetch_historical_candles(
        self,
        symbol: str = "AAPL",
        timeframe: str = "5m",
        days: int = 360,
        limit: int | None = None,
        adjustment: str = "split",
    ) -> pd.DataFrame:
        """
        Fetch historical stock candles from Alpaca Markets API v2.
        Supports multi-page token pagination up to requested limit/days.
        """
        ticker = self.format_symbol(symbol)
        tf_mapped = ALPACA_TIMEFRAMES.get(timeframe.lower(), "5Min")
        headers = self._get_headers()

        # If credentials are missing, log warning and return empty df (caller can fallback)
        if "APCA-API-KEY-ID" not in headers or not headers["APCA-API-KEY-ID"]:
            logger.warning(
                f"[AlpacaStockAdapter] No Alpaca API credentials configured. Cannot fetch live data for {ticker}."
            )
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)
        start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(
            f"[AlpacaStockAdapter] Fetching ~{days} days of historical candles for {ticker} ({timeframe})..."
        )

        all_bars = []
        page_token: str | None = None
        max_pages = 25  # Up to 250,000 bars
        page_count = 0

        while page_count < max_pages:
            page_count += 1
            query_params = (
                f"timeframe={tf_mapped}&start={start_str}&end={end_str}"
                f"&limit=10000&adjustment={adjustment}&feed=sip"
            )
            if page_token:
                query_params += f"&page_token={page_token}"

            url = f"{self.base_url}/{ticker}/bars?{query_params}"

            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status != 200:
                        logger.error(
                            f"[AlpacaStockAdapter] HTTP {resp.status} for {ticker}: {resp.read().decode('utf-8')}"
                        )
                        break

                    data = json.loads(resp.read().decode("utf-8"))
                    bars = data.get("bars", [])
                    if not bars:
                        break

                    for b in bars:
                        # Alpaca format: t (RFC-3339), o, h, l, c, v
                        all_bars.append(
                            {
                                "timestamp": b["t"],
                                "open": float(b["o"]),
                                "high": float(b["h"]),
                                "low": float(b["l"]),
                                "close": float(b["c"]),
                                "volume": float(b["v"]),
                            }
                        )

                    page_token = data.get("next_page_token")
                    if not page_token:
                        break

                    # Slight throttle to prevent 429 rate limits
                    time.sleep(0.1)

            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
                if e.code == 429:
                    logger.warning(
                        "[AlpacaStockAdapter] Rate limited (429). Retrying in 1s..."
                    )
                    time.sleep(1.0)
                    continue
                logger.error(f"[AlpacaStockAdapter] HTTP {e.code} error: {err_body}")
                break
            except Exception as e:
                logger.error(
                    f"[AlpacaStockAdapter] Network error fetching candles: {e}"
                )
                break

        if not all_bars:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

        df = pd.DataFrame(all_bars)
        df = self.validate_and_normalize_df(df)

        if limit and len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)

        logger.info(
            f"[AlpacaStockAdapter] Successfully fetched {len(df)} candles for {ticker} ({timeframe})."
        )
        return df
