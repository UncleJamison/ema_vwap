"""
Data Loader Module for Gemini, KuCoin, and Synthetic Market Data.
Refactored to orchestrate pluggable BaseDataProvider adapters with persistent SQLite database caching.
"""

import pandas as pd

from src.database import CandleDatabase
from src.providers import (
    STANDARD_TF_ORDER,
    GeminiAdapter,
    KuCoinAdapter,
    SyntheticAdapter,
    normalize_symbol,
    registry,
)

db = CandleDatabase()

__all__ = [
    "EXCHANGE_NATIVE_TFS",
    "DataLoader",
    "get_donor_timeframe",
    "normalize_symbol",
]

# Native timeframes supported by each exchange REST API (re-exported for backward compatibility)
EXCHANGE_NATIVE_TFS: dict[str, set[str]] = {
    "gemini": {"1m", "5m", "15m", "30m", "1h", "6h", "1d"},
    "kucoin": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"},
    "synthetic": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"},
    "alpaca": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"},
    "polygon": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"},
    "synthetic_stock": {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"},
}

_TF_ORDER = STANDARD_TF_ORDER


def get_donor_timeframe(exchange: str, target_tf: str) -> str | None:
    """
    Return the largest native timeframe smaller than target_tf that can be resampled up.
    Returns None if natively supported or no donor exists.
    """
    provider = registry.get(exchange)
    return provider.get_donor_timeframe(target_tf)


class DataLoader:
    """Fetches market candle data from provider adapters with database caching and resampling."""

    @staticmethod
    def apply_split_adjustment(
        df: pd.DataFrame,
        split_date: str,
        split_ratio: float = 2.0,
    ) -> pd.DataFrame:
        """
        Adjust historical OHLCV data for stock split corporate actions occurring on split_date.
        For bars before split_date:
        - Divide Open, High, Low, Close by split_ratio.
        - Multiply Volume by split_ratio.
        """
        if df is None or df.empty or split_ratio <= 0:
            return df if df is not None else pd.DataFrame()

        df = df.copy()
        split_ts = pd.to_datetime(split_date, utc=True)
        ts_col = pd.to_datetime(df["timestamp"], utc=True)
        mask = ts_col < split_ts

        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df.loc[mask, col] = df.loc[mask, col] / split_ratio
        if "volume" in df.columns:
            df.loc[mask, "volume"] = df.loc[mask, "volume"] * split_ratio
        return df

    @staticmethod
    def format_gemini_symbol(symbol: str) -> str:
        """Convert standard symbol format (e.g. BTC/USD or BTC/USDT) to Gemini format (btcusd)."""
        adapter: GeminiAdapter = registry.get("gemini")  # type: ignore
        return adapter.format_symbol(symbol)

    @staticmethod
    def format_kucoin_symbol(symbol: str) -> str:
        """Convert standard symbol format (e.g. BTC/USD or BTC/USDT) to KuCoin format (BTC-USDT)."""
        adapter: KuCoinAdapter = registry.get("kucoin")  # type: ignore
        return adapter.format_symbol(symbol)

    @classmethod
    def fetch_kucoin_historical(
        cls, symbol: str = "BTC/USDT", timeframe: str = "5m", days: int = 180
    ) -> pd.DataFrame:
        """
        Fetch up to `days` of historical intraday candles from KuCoin using paginated requests.
        Saves candles to SQLite database and upserts derived higher timeframes.
        """
        provider = registry.get("kucoin")
        df = provider.fetch_historical_candles(
            symbol=symbol, timeframe=timeframe, days=days
        )

        if df.empty:
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
            return cls.generate_synthetic_candles(
                symbol=symbol,
                timeframe=timeframe,
                num_bars=days * (86400 // step_seconds),
            )

        # Save fetched candles to SQLite database
        db.save_candles(exchange="kucoin", symbol=symbol, timeframe=timeframe, df=df)

        # Upsert derived higher timeframes
        cls.resample_and_upsert(
            exchange="kucoin", symbol=symbol, base_timeframe=timeframe, df=df
        )

        return df

    @classmethod
    def fetch_gemini_historical(
        cls, symbol: str = "BTC/USD", timeframe: str = "5m", days: int = 180
    ) -> pd.DataFrame:
        """Fetch historical candles from Gemini REST API and cache in SQLite.

        Gemini supports: 1m, 5m, 15m, 30m, 1h, 6h, 1d.
        For unsupported timeframes (3m, 2h, 4h), fetches donor TF and resamples up.
        """
        # Route unsupported TFs through donor resampling
        donor_tf = get_donor_timeframe("gemini", timeframe)
        if donor_tf is not None:
            print(
                f"[DataLoader] Gemini does not natively support {timeframe}. "
                f"Fetching {donor_tf} and resampling up..."
            )
            donor_df = cls.fetch_gemini_historical(
                symbol=symbol, timeframe=donor_tf, days=days
            )
            if donor_df.empty:
                return donor_df
            cls.resample_and_upsert(
                exchange="gemini",
                symbol=symbol,
                df=donor_df,
                base_timeframe=donor_tf,
                target_tfs=[timeframe],
            )
            return db.load_candles(
                exchange="gemini", symbol=symbol, timeframe=timeframe
            )

        provider = registry.get("gemini")
        df = provider.fetch_historical_candles(
            symbol=symbol, timeframe=timeframe, days=days
        )

        if df.empty:
            print(
                "[DataLoader] Gemini request returned empty. Using synthetic generator."
            )
            return cls.generate_synthetic_candles(
                symbol=symbol, timeframe=timeframe, num_bars=min(days * 288, 1000)
            )

        db.save_candles(exchange="gemini", symbol=symbol, timeframe=timeframe, df=df)
        cls.resample_and_upsert(
            exchange="gemini", symbol=symbol, base_timeframe=timeframe, df=df
        )
        return df

    @classmethod
    def generate_synthetic_candles(
        cls,
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        num_bars: int = 500,
        start_price: float | None = None,
        seed: int | None = 42,
    ) -> pd.DataFrame:
        """Generate realistic synthetic OHLCV data using SyntheticAdapter."""
        synth_adapter: SyntheticAdapter = registry.get("synthetic")  # type: ignore
        return synth_adapter.generate_candles(
            symbol=symbol,
            timeframe=timeframe,
            num_bars=num_bars,
            start_price=start_price,
            seed=seed,
        )

    @classmethod
    def resample_and_upsert(
        cls,
        exchange: str,
        symbol: str,
        df: pd.DataFrame,
        base_timeframe: str = "5m",
        target_tfs: list[str] | None = None,
    ) -> int:
        """
        Resample base OHLCV candles into higher timeframes and upsert to SQLite.

        Args:
            target_tfs: Optional explicit list of TFs to derive. When None, derives
                        all higher TFs from base_timeframe.
        """
        if df.empty or len(df) < 5:
            return 0

        timeframe_order = STANDARD_TF_ORDER
        if target_tfs is not None:
            base_idx = (
                timeframe_order.index(base_timeframe)
                if base_timeframe in timeframe_order
                else -1
            )
            tfs_to_build = [
                tf
                for tf in target_tfs
                if tf in timeframe_order and timeframe_order.index(tf) > base_idx
            ]
        elif base_timeframe in timeframe_order:
            base_idx = timeframe_order.index(base_timeframe)
            tfs_to_build = timeframe_order[base_idx + 1 :]
        else:
            tfs_to_build = ["15m", "30m", "1h", "2h", "4h", "6h", "1d"]

        total_upserted = 0

        resample_freq_map = {
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

        for tf in tfs_to_build:
            resample_freq = resample_freq_map.get(tf)
            if not resample_freq:
                continue

            df_temp = df.copy()
            try:
                df_temp["timestamp"] = pd.to_datetime(
                    df_temp["timestamp"], utc=True, format="ISO8601"
                )
            except Exception:
                df_temp["timestamp"] = pd.to_datetime(df_temp["timestamp"], utc=True)
            df_temp = df_temp.set_index("timestamp")

            resampled = (
                df_temp.resample(resample_freq)
                .agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                    }
                )
                .dropna()
                .reset_index()
            )

            if not resampled.empty:
                count = db.save_candles(
                    exchange=exchange, symbol=symbol, timeframe=tf, df=resampled
                )
                total_upserted += count

        return total_upserted

    @classmethod
    def load_candles(
        cls,
        exchange: str = "kucoin",
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        limit: int = 500,
        days: int | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Main interface to fetch candles.

        Transparently handles timeframes not natively supported by an exchange
        by fetching the best available donor TF and resampling up.
        Checks SQLite cache first; fetches fresh from provider API when needed.
        """
        exchange_lower = exchange.lower()
        donor_tf = get_donor_timeframe(exchange_lower, timeframe)
        if donor_tf is not None and exchange_lower not in ("synthetic",):
            print(
                f"[DataLoader] {exchange} does not natively support {timeframe}. "
                f"Fetching {donor_tf} and resampling to {timeframe}..."
            )
            cls.load_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=donor_tf,
                limit=limit,
                days=days,
                use_cache=use_cache,
            )
            donor_df = db.load_candles(
                exchange=exchange_lower, symbol=symbol, timeframe=donor_tf
            )
            if not donor_df.empty:
                cls.resample_and_upsert(
                    exchange=exchange_lower,
                    symbol=symbol,
                    df=donor_df,
                    base_timeframe=donor_tf,
                    target_tfs=[timeframe],
                )

        tf_per_day_map = {
            "1m": 1440,
            "3m": 480,
            "5m": 288,
            "15m": 96,
            "30m": 48,
            "1h": 24,
            "2h": 12,
            "4h": 6,
            "6h": 4,
            "1d": 1,
        }
        if days is not None:
            req_candles = days * tf_per_day_map.get(timeframe, 288)
            target_limit = max(limit or 0, req_candles)
        else:
            target_limit = limit or 500

        # Check local DB cache (exchanges only; synthetic is always generated fresh)
        if use_cache and exchange_lower != "synthetic":
            cached_df = db.load_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=target_limit,
            )
            if len(cached_df) >= target_limit:
                print(
                    f"[DataLoader] Loaded {len(cached_df)} candles for {symbol} ({exchange}) directly from SQLite DB."
                )
                return cached_df

        # Fetch fresh data when cache is missing or insufficient
        if exchange_lower == "kucoin":
            days_to_fetch = days if days is not None else max(1, target_limit // 288)
            df = cls.fetch_kucoin_historical(
                symbol=symbol, timeframe=timeframe, days=days_to_fetch
            )
        elif exchange_lower == "gemini":
            days_to_fetch = days if days is not None else max(1, target_limit // 288)
            df = cls.fetch_gemini_historical(
                symbol=symbol, timeframe=timeframe, days=days_to_fetch
            )
        else:
            df = cls.generate_synthetic_candles(
                symbol=symbol, timeframe=timeframe, num_bars=target_limit
            )

        if days is None and limit and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)

        return df

    @classmethod
    def fetch_and_update_candles(
        cls,
        exchange: str = "kucoin",
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        limit: int = 200,
    ) -> pd.DataFrame:
        """
        Fetch latest live candles from provider API, upsert into SQLite DB cache,
        and return the most recent `limit` candles from DB for real-time strategy evaluation.
        Falls back to DB cache gracefully on network or rate limit failure.
        """
        exchange_lower = exchange.lower()
        if exchange_lower == "synthetic":
            return cls.generate_synthetic_candles(
                symbol=symbol, timeframe=timeframe, num_bars=limit
            )

        tf_days_map = {
            "1m": 1,
            "3m": 1,
            "5m": 1,
            "15m": 2,
            "30m": 4,
            "1h": 8,
            "2h": 16,
            "4h": 32,
            "6h": 48,
            "1d": 180,
        }
        days_to_fetch = tf_days_map.get(timeframe, 1)

        try:
            donor_tf = get_donor_timeframe(exchange_lower, timeframe)
            fetch_tf = donor_tf if donor_tf is not None else timeframe

            provider = registry.get(exchange_lower)
            fresh_df = provider.fetch_historical_candles(
                symbol=symbol, timeframe=fetch_tf, days=days_to_fetch
            )

            if not fresh_df.empty:
                db.save_candles(
                    exchange=exchange_lower,
                    symbol=symbol,
                    timeframe=fetch_tf,
                    df=fresh_df,
                )
                if donor_tf is not None:
                    cls.resample_and_upsert(
                        exchange=exchange_lower,
                        symbol=symbol,
                        df=fresh_df,
                        base_timeframe=donor_tf,
                        target_tfs=[timeframe],
                    )
        except Exception as e:
            print(
                f"[DataLoader] Live fetch failed for {symbol} ({exchange_lower}): {e}. Using cached data."
            )

        # Return latest `limit` candles from DB
        df = db.load_candles(
            exchange=exchange_lower,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )
        return df
