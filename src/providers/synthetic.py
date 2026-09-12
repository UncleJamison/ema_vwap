"""
Synthetic Market Data Provider Adapter.
Generates realistic multi-regime OHLCV candle data with trending, volatility, and volume simulation.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from src.providers.base import BaseDataProvider, normalize_symbol


class SyntheticAdapter(BaseDataProvider):
    """Synthetic OHLCV market candle data generator."""

    name: ClassVar[str] = "synthetic"
    asset_type: ClassVar[str] = "synthetic"
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

    # Default baseline prices for various assets
    DEFAULT_BASE_PRICES: ClassVar[dict[str, float]] = {
        "BONK": 0.000022,
        "PEPE": 0.000010,
        "SHIB": 0.000017,
        "FLOKI": 0.00015,
        "DOGE": 0.14,
        "SOL": 150.0,
        "ETH": 3400.0,
        "ZEC": 42.0,
        "AAPL": 220.0,
        "SPY": 540.0,
        "NVDA": 125.0,
        "TSLA": 210.0,
        "MSFT": 440.0,
        "BTC": 65000.0,
    }

    def format_symbol(self, symbol: str) -> str:
        """Format symbol to canonical BASE/QUOTE for synthetic generator."""
        return normalize_symbol(symbol)

    def get_initial_price(self, symbol: str, start_price: float | None = None) -> float:
        """Resolve initial starting price for a given asset symbol."""
        if start_price is not None and start_price != 65000.0:
            return float(start_price)

        sym_upper = symbol.upper()
        for token, price in self.DEFAULT_BASE_PRICES.items():
            if token in sym_upper:
                return price
        return 65000.0

    def generate_candles(
        self,
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        num_bars: int = 500,
        start_price: float | None = None,
        seed: int | None = 42,
        end_time: pd.Timestamp | str | None = None,
    ) -> pd.DataFrame:
        """Generate realistic synthetic OHLCV data."""
        if seed is not None:
            np.random.seed(seed)

        init_price = self.get_initial_price(symbol, start_price)

        tf_freq_map = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "1d": "1D",
        }
        freq = tf_freq_map.get(timeframe, "5min")

        if end_time is not None:
            end_ts = pd.Timestamp(end_time)
            if end_ts.tz is None:
                end_ts = end_ts.tz_localize("UTC")
            else:
                end_ts = end_ts.tz_convert("UTC")
        else:
            end_ts = pd.Timestamp.now(tz="UTC").floor("min")

        timestamps = pd.date_range(end=end_ts, periods=num_bars, freq=freq)
        prices = [init_price]
        volumes = []

        current_trend = 0.0003
        for i in range(1, num_bars):
            if i % 60 == 0:
                current_trend = np.random.choice(
                    [0.0008, -0.0008, 0.0002, -0.0002, 0.0]
                )

            volatility = 0.003
            noise = np.random.normal(0, volatility)
            new_price = prices[-1] * (1.0 + current_trend + noise)
            prices.append(new_price)

            base_volume = 15.0 + np.random.exponential(5.0)
            if abs(current_trend) > 0.0005:
                base_volume *= np.random.uniform(1.3, 2.5)
            volumes.append(base_volume)

        volumes.insert(0, 15.0)
        closes = np.array(prices)
        highs = closes * (1.0 + np.abs(np.random.normal(0.001, 0.001, num_bars)))
        lows = closes * (1.0 - np.abs(np.random.normal(0.001, 0.001, num_bars)))
        opens = np.roll(closes, 1)
        opens[0] = closes[0]

        df = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": opens,
                "high": np.maximum(highs, np.maximum(opens, closes)),
                "low": np.minimum(lows, np.minimum(opens, closes)),
                "close": closes,
                "volume": volumes,
            }
        )
        return self.validate_and_normalize_df(df)

    def fetch_historical_candles(
        self,
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        days: int = 180,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Fetch historical candles via synthetic simulation."""
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
        num_bars = limit if limit else days * (86400 // step_seconds)
        num_bars = max(50, min(num_bars, 50000))
        return self.generate_candles(
            symbol=symbol, timeframe=timeframe, num_bars=num_bars
        )
