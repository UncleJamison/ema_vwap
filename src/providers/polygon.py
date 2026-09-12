"""
Polygon.io Stock Data Provider Adapter.
Implements Polygon Aggregates API v2 with date-range chunking, rate-limit handling,
and split-adjusted OHLCV aggregate bars.
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

# Timeframe translation to (multiplier, timespan)
POLYGON_TIMEFRAMES: dict[str, tuple[int, str]] = {
    "1m": (1, "minute"),
    "3m": (3, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "1h": (1, "hour"),
    "2h": (2, "hour"),
    "4h": (4, "hour"),
    "1d": (1, "day"),
}


class PolygonStockAdapter(BaseDataProvider):
    """
    Data adapter for Polygon.io Stocks API.
    Retrieves historical aggregates with split adjustment support.
    """

    name: ClassVar[str] = "polygon"
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
    base_url: ClassVar[str] = "https://api.polygon.io/v2/aggs/ticker"

    def __init__(self, settings_manager: SettingsManager | None = None):
        self.settings = settings_manager or SettingsManager()

    def format_symbol(self, symbol: str) -> str:
        """Strip quote currency and special characters (e.g., 'TSLA/USD' -> 'TSLA')."""
        clean = symbol.strip().upper().replace("-", "/").replace("_", "")
        if "/" in clean:
            return clean.split("/")[0]
        return clean

    def fetch_historical_candles(
        self,
        symbol: str = "AAPL",
        timeframe: str = "5m",
        days: int = 180,
        limit: int | None = None,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch historical stock candles from Polygon.io Aggregates API.
        """
        ticker = self.format_symbol(symbol)
        tf_info = POLYGON_TIMEFRAMES.get(timeframe.lower(), (5, "minute"))
        mult, timespan = tf_info

        api_key = self.settings.get_polygon_api_key()
        if not api_key:
            logger.warning(
                f"[PolygonStockAdapter] No Polygon.io API key configured. Cannot fetch live data for {ticker}."
            )
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")

        logger.info(
            f"[PolygonStockAdapter] Fetching ~{days} days of historical candles for {ticker} ({timeframe})..."
        )

        all_bars = []
        adj_param = "true" if adjusted else "false"
        url = (
            f"{self.base_url}/{ticker}/range/{mult}/{timespan}/{start_str}/{end_str}"
            f"?adjusted={adj_param}&sort=asc&limit=50000&apiKey={api_key}"
        )

        next_url: str | None = url
        page_count = 0
        max_pages = 20

        while next_url and page_count < max_pages:
            page_count += 1
            # Append API key if next_url doesn't already contain it
            if "apiKey=" not in next_url:
                next_url += f"&apiKey={api_key}"

            req = urllib.request.Request(
                next_url, headers={"User-Agent": "EMA-VWAP-Stock-Engine/1.0"}
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status != 200:
                        logger.error(
                            f"[PolygonStockAdapter] HTTP {resp.status} for {ticker}: {resp.read().decode('utf-8')}"
                        )
                        break

                    data = json.loads(resp.read().decode("utf-8"))
                    results = data.get("results", [])
                    if not results:
                        break

                    for r in results:
                        # Polygon format: t (Unix ms), o, h, l, c, v
                        ts_sec = r["t"] / 1000.0
                        ts_iso = datetime.fromtimestamp(
                            ts_sec, tz=timezone.utc
                        ).isoformat()
                        all_bars.append(
                            {
                                "timestamp": ts_iso,
                                "open": float(r["o"]),
                                "high": float(r["h"]),
                                "low": float(r["l"]),
                                "close": float(r["c"]),
                                "volume": float(r["v"]),
                            }
                        )

                    next_url = data.get("next_url")
                    time.sleep(0.1)

            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
                if e.code == 429:
                    logger.warning(
                        "[PolygonStockAdapter] Rate limited (429). Retrying in 1s..."
                    )
                    time.sleep(1.0)
                    continue
                logger.error(f"[PolygonStockAdapter] HTTP {e.code} error: {err_body}")
                break
            except Exception as e:
                logger.error(f"[PolygonStockAdapter] Network error: {e}")
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
            f"[PolygonStockAdapter] Successfully fetched {len(df)} candles for {ticker} ({timeframe})."
        )
        return df
