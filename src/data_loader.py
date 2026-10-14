"""
Data Loader Module for Gemini, KuCoin, and Synthetic Market Data.
Refactored to orchestrate pluggable BaseDataProvider adapters with persistent SQLite database caching.
"""

import time
from typing import ClassVar

import pandas as pd

from src.config import get_bars_per_day, get_timeframe_spec
from src.database import CandleDatabase
from src.providers import (
    STANDARD_TF_ORDER,
    GeminiAdapter,
    KuCoinAdapter,
    SyntheticAdapter,
    normalize_symbol,
    registry,
)

db = CandleDatabase(domain="candles")

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
    Return the donor timeframe for resampling.
    For KuCoin, use explicit donor mapping for specific timeframes.
    Returns None if natively supported (no resampling needed) or no donor exists.
    """
    # KuCoin-specific donor overrides based on actual historical data availability:
    # Native: 1m, 5m, 15m, 1h, 8h, 12h, 1d
    if exchange.lower() == "kucoin":
        kucoin_donor_map = {
            "3m": "1m",  # use 1m as donor for 3m
            "30m": "15m",  # use 15m as donor for 30m
            "2h": "1h",  # use 1h as donor for 2h
            "4h": "1h",  # use 1h as donor for 4h
            "6h": "1h",  # use 1h as donor for 6h
        }
        if target_tf in kucoin_donor_map:
            return kucoin_donor_map[target_tf]
        # Native timeframes that don't need resampling
        native_no_resample = {"1m", "5m", "15m", "1h", "8h", "12h", "1d"}
        if target_tf in native_no_resample:
            return None

    provider = registry.get(exchange)
    return provider.get_donor_timeframe(target_tf)


class DataLoader:
    """Fetches market candle data from provider adapters with database caching and resampling."""

    # Donor-fetch memoization (ema_vwap-qx8o): throttle redundant donor fetch+resample
    _donor_fetch_cache: ClassVar[dict[tuple, float]] = {}
    _DONOR_CACHE_TTL: ClassVar[float] = 300.0  # seconds

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
        cls, symbol: str = "BTC/USDT", timeframe: str = "5m", days: int | None = None
    ) -> pd.DataFrame:
        """
        Fetch up to `days` of historical intraday candles from KuCoin using paginated requests.
        Saves candles to SQLite database and upserts derived higher timeframes.
        """

        provider = registry.get("kucoin")
        df = provider.fetch_historical_candles(
            symbol=symbol, timeframe=timeframe, days=days
        )

        # Explicit 1m -> 3m upsampling when 3m data is insufficient
        if timeframe == "3m" and (
            df.empty or (days is not None and len(df) < max(10, int(days * 480 * 0.3)))
        ):

            print(
                f"[DataLoader] 3m insufficient ({len(df)} bars, requested {days} days). Fetching 1m and upsampling..."
            )
            donor_df = cls.fetch_kucoin_historical(
                symbol=symbol, timeframe="1m", days=days
            )
            if not donor_df.empty and len(donor_df) > 0:
                # Save 1m and resample to 3m
                cls.resample_and_upsert(
                    exchange="kucoin",
                    symbol=symbol,
                    base_timeframe="1m",
                    df=donor_df,
                    target_tfs=["3m"],
                )
                # Load upsampled 3m from DB
                upsampled = db.load_candles(
                    exchange="kucoin",
                    symbol=symbol,
                    timeframe="3m",
                    limit=max(1, int(days * 480)) if days else 500,
                )
                if not upsampled.empty:
                    return upsampled
            # If 1m donor also fails, fall through to synthetic

        if df.empty:
            bars_per_day = get_bars_per_day(timeframe, "crypto")
            synth_df = cls.generate_synthetic_candles(
                symbol=symbol,
                timeframe=timeframe,
                num_bars=min(days * bars_per_day, 1000),
            )
            # Save synthetic with explicit tag for audit
            db.save_candles(
                exchange="synthetic", symbol=symbol, timeframe=timeframe, df=synth_df
            )
            return synth_df

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
            bars_per_day = get_bars_per_day(timeframe, "crypto")
            return cls.generate_synthetic_candles(
                symbol=symbol,
                timeframe=timeframe,
                num_bars=min(days * bars_per_day, 1000),
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

    @staticmethod
    def _resample_df(
        df: pd.DataFrame, base_timeframe: str, target_tf: str
    ) -> pd.DataFrame:
        """Resample a DataFrame from base_timeframe to target_tf (ema_vwap-9jfi).

        Does not persist to database; returns resampled DataFrame with standard
        OHLCV columns. Used by BatchOptimizer to avoid redundant API fetches.
        """
        if df.empty or len(df) < 5:
            return pd.DataFrame()

        timeframe_order = STANDARD_TF_ORDER
        if base_timeframe not in timeframe_order or target_tf not in timeframe_order:
            return pd.DataFrame()
        base_idx = timeframe_order.index(base_timeframe)
        target_idx = timeframe_order.index(target_tf)
        if target_idx <= base_idx:
            return pd.DataFrame()  # target is finer or same, cannot upsample

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
        resample_freq = resample_freq_map.get(target_tf)
        if not resample_freq:
            return pd.DataFrame()

        try:
            df_temp = df.copy()
            if "timestamp" in df_temp.columns:
                df_temp = df_temp.set_index("timestamp")
            if not isinstance(df_temp.index, pd.DatetimeIndex):
                df_temp.index = pd.to_datetime(df_temp.index, utc=True)

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
            return resampled
        except Exception:
            return pd.DataFrame()

    @classmethod
    def _fetch_and_store(
        cls,
        exchange: str,
        symbol: str,
        timeframe: str,
        days: int | None = None,
        limit: int | None = None,
        donor_tf: str | None = None,
    ) -> pd.DataFrame:
        """
        Shared fetch + upsert + resample logic (ema_vwap-plkq).
        Used by both load_candles and fetch_and_update_candles to avoid duplication.

        Args:
            exchange: Exchange name (kucoin, gemini, etc.)
            symbol: Trading symbol
            timeframe: Target timeframe
            days: Days of history to fetch (overrides limit-based calculation)
            limit: Max candles to return (used for days calculation if days is None)
            donor_tf: If provided, resample from this donor timeframe
        """
        exchange_lower = exchange.lower()
        spec = get_timeframe_spec(timeframe, "crypto")
        tf_per_day = spec.bars_per_day_crypto

        if days is None and limit is not None:
            days = max(1, int(limit // tf_per_day))

        if donor_tf is not None:
            # Resample path: fetch donor TF, upsert, then resample
            df = cls._fetch_and_store(
                exchange=exchange,
                symbol=symbol,
                timeframe=donor_tf,
                days=days,
                limit=limit,
                donor_tf=None,
            )
            return cls.resample_and_upsert(
                exchange=exchange_lower,
                symbol=symbol,
                df=df,
                base_timeframe=donor_tf,
                target_tfs=[timeframe],
            )

        # Direct fetch path
        if exchange_lower == "kucoin":
            df = cls.fetch_kucoin_historical(
                symbol=symbol, timeframe=timeframe, days=days
            )
        elif exchange_lower == "gemini":
            df = cls.fetch_gemini_historical(
                symbol=symbol, timeframe=timeframe, days=days
            )
        else:
            df = cls.generate_synthetic_candles(
                symbol=symbol, timeframe=timeframe, num_bars=days * tf_per_day
            )

        if not df.empty:
            db.save_candles(
                exchange=exchange_lower,
                symbol=symbol,
                timeframe=timeframe,
                df=df,
            )

        if limit and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)

        return df

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
            # Donor-fetch memoization (ema_vwap-qx8o): batch sweeps repeatedly hit the
            # same (exchange, symbol, donor_tf, limit, days) combination; skip the
            # redundant recursive fetch + resample when done recently.
            donor_key = (exchange_lower, str(symbol), donor_tf, int(limit or 0), days)
            now = time.time()
            cached_ts = cls._donor_fetch_cache.get(donor_key)
            if cached_ts is not None and (now - cached_ts) < cls._DONOR_CACHE_TTL:
                pass  # Donor fetched recently; skip redundant fetch + resample
            else:
                # Recursively fetch and resample the donor timeframe
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
                cls._donor_fetch_cache[donor_key] = now

        spec = get_timeframe_spec(timeframe, "crypto")
        tf_per_day = spec.bars_per_day_crypto

        if days is not None:
            req_candles = int(float(days) * float(tf_per_day))
            target_limit = max(int(limit or 0), req_candles)
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

        # Fetch fresh data using shared method (ema_vwap-plkq)
        df = cls._fetch_and_store(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            limit=target_limit,
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

        # Use shared fetch + store method (ema_vwap-plkq)
        try:
            df = cls._fetch_and_store(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
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
