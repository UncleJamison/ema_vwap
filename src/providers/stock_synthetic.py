"""
Synthetic US Stock Market Data Provider Adapter.
Generates realistic deterministic intraday stock candle series with US market hours (09:30-16:00 ET),
overnight gap jumps, U-shaped intraday volume profiles, and optional simulated corporate actions (splits).
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from src.providers.base import BaseDataProvider


class SyntheticStockAdapter(BaseDataProvider):
    """
    High-fidelity deterministic synthetic stock candle provider.
    Ideal for unit testing, offline development, and testing corporate split actions.
    """

    name: ClassVar[str] = "synthetic_stock"
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

    # Standard stock base prices
    DEFAULT_PRICES: ClassVar[dict[str, float]] = {
        "AAPL": 220.0,
        "SPY": 550.0,
        "TSLA": 210.0,
        "NVDA": 125.0,
        "QQQ": 480.0,
        "MSFT": 440.0,
        "AMZN": 180.0,
    }

    def format_symbol(self, symbol: str) -> str:
        """Strip quote currency and special characters (e.g., 'AAPL/USD' -> 'AAPL')."""
        clean = symbol.strip().upper().replace("-", "/").replace("_", "")
        if "/" in clean:
            return clean.split("/")[0]
        return clean

    def fetch_historical_candles(
        self,
        symbol: str = "AAPL",
        timeframe: str = "5m",
        days: int = 30,
        limit: int | None = None,
        include_split: bool = False,
        split_ratio: float = 2.0,
    ) -> pd.DataFrame:
        """
        Generate synthetic historical stock candles adhering to US market hours.
        """
        ticker = self.format_symbol(symbol)
        start_price = self.DEFAULT_PRICES.get(ticker, 150.0)

        tf_minutes_map = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "1d": 390,
        }
        bar_mins = tf_minutes_map.get(timeframe.lower(), 5)

        # Build trading days backwards from today
        end_date = datetime.now(timezone.utc).date()
        trading_days = []
        cur_date = end_date - timedelta(days=days * 2)  # buffer for weekends

        while len(trading_days) < days and cur_date <= end_date:
            # Skip Saturday (5) and Sunday (6)
            if cur_date.weekday() < 5:
                trading_days.append(cur_date)
            cur_date += timedelta(days=1)

        # Seed random generator deterministically based on ticker
        seed = sum(ord(c) for c in ticker) + days + bar_mins
        np.random.seed(seed)

        bars: list[dict[str, Any]] = []
        current_close = start_price
        total_days = len(trading_days)
        split_day_idx = total_days // 2 if include_split else -1

        for day_idx, t_date in enumerate(trading_days):
            # Simulate overnight gap (from previous close to today's open)
            if day_idx > 0:
                overnight_return = np.random.normal(
                    0.0005, 0.008
                )  # slight overnight drift
                current_close = current_close * (1.0 + overnight_return)

            # Apply stock split on split day if enabled (e.g. 2:1 split cuts price in half)
            if day_idx == split_day_idx:
                current_close /= split_ratio

            # Generate intraday bars from 09:30 to 16:00 ET (13:30 to 20:00 UTC during EDT)
            # Standard US/Eastern 09:30 to 16:00 has 390 minutes
            day_minutes = 390
            num_bars_today = max(1, day_minutes // bar_mins)

            for b in range(num_bars_today):
                bar_offset_min = b * bar_mins
                # 09:30 ET is 13:30 UTC
                bar_time = datetime(
                    t_date.year,
                    t_date.month,
                    t_date.day,
                    13,
                    30,
                    0,
                    tzinfo=timezone.utc,
                ) + timedelta(minutes=bar_offset_min)

                # Intraday volatility and mean reversion
                pct_change = np.random.normal(0.0, 0.002 * math.sqrt(bar_mins / 5.0))
                o = current_close
                c = o * (1.0 + pct_change)
                h = max(o, c) * (1.0 + abs(np.random.normal(0.0, 0.0015)))
                low = min(o, c) * (1.0 - abs(np.random.normal(0.0, 0.0015)))

                # U-shaped volume curve: higher at market open and close
                intraday_progress = b / max(1, num_bars_today - 1)
                u_mult = 1.0 + 2.5 * ((intraday_progress - 0.5) ** 2)
                base_vol = 10000.0 * (bar_mins / 5.0)
                vol = float(base_vol * u_mult * np.random.uniform(0.7, 1.4))

                bars.append(
                    {
                        "timestamp": bar_time.isoformat(),
                        "open": round(o, 4),
                        "high": round(h, 4),
                        "low": round(low, 4),
                        "close": round(c, 4),
                        "volume": round(vol, 2),
                    }
                )
                current_close = c

        df = pd.DataFrame(bars)
        df = self.validate_and_normalize_df(df)

        if limit and len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)

        return df
