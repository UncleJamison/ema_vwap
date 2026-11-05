"""
Data Loader Module for Gemini, KuCoin, and Synthetic Market Data.
Refactored to orchestrate pluggable BaseDataProvider adapters with persistent SQLite database caching.
"""

import time
from typing import ClassVar

import pandas as pd

from src.config import get_timeframe_spec
from src.database import CandleDatabase
from src.pg_database import load_candles_pg, save_candles_pg
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
    def _cache_is_fresh(df: pd.DataFrame, timeframe: str) -> bool:
        if df.empty:
            return False

        timeframe_minutes = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "8h": 480,
            "12h": 720,
            "1d": 1440,
        }
        period_minutes = timeframe_minutes.get(timeframe.lower(), 5)
        newest_ts = pd.to_datetime(df["timestamp"].iloc[-1], utc=True)
        age_seconds = (pd.Timestamp.now(tz="UTC") - newest_ts).total_seconds()
        return age_seconds <= period_minutes * 60 * 5

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

    @staticmethod
    def resolve_canonical_symbol(
        canonical_symbol: str, live_mode: bool = False
    ) -> tuple[str, str]:
        """
        Resolve a canonical symbol (e.g., 'BTC/USD') to exchange-specific symbol.

        Args:
            canonical_symbol: The canonical symbol (e.g., 'BTC/USD')
            live_mode: If True, use prefer_live exchange; otherwise prefer_history

        Returns:
            Tuple of (exchange, exchange_specific_symbol)
        """
        from src.settings import SettingsManager

        mgr = SettingsManager()
        return mgr.resolve_exchange_symbol(canonical_symbol, live_mode)

    @staticmethod
    async def aresolve_canonical_symbol(
        canonical_symbol: str, live_mode: bool = False
    ) -> tuple[str, str]:
        """
        Async variant of resolve_canonical_symbol() - safe from the main event loop.

        Args:
            canonical_symbol: The canonical symbol (e.g., 'BTC/USD')
            live_mode: If True, use prefer_live exchange; otherwise prefer_history

        Returns:
            Tuple of (exchange, exchange_specific_symbol)
        """
        from src.settings import SettingsManager

        mgr = SettingsManager()
        return await mgr.aresolve_exchange_symbol(canonical_symbol, live_mode)

    @classmethod
    async def fetch_kucoin_historical(
        cls, symbol: str = "BTC/USDT", timeframe: str = "5m", days: int | None = None
    ) -> pd.DataFrame:
        """
        Fetch up to `days` of historical intraday candles from KuCoin using paginated requests.
        """

        provider = registry.get("kucoin")
        import asyncio

        df = await asyncio.to_thread(
            provider.fetch_historical_candles,
            symbol=symbol,
            timeframe=timeframe,
            days=days,
        )

        return df

    @classmethod
    async def fetch_gemini_historical(
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
            donor_df = await cls.fetch_gemini_historical(
                symbol=symbol, timeframe=donor_tf, days=days
            )
            if donor_df.empty:
                raise RuntimeError(
                    f"Gemini returned no {donor_tf} candles for {symbol}; "
                    f"cannot build {timeframe} history."
                )
            await cls.resample_and_upsert(
                exchange="gemini",
                symbol=symbol,
                df=donor_df,
                base_timeframe=donor_tf,
                target_tfs=[timeframe],
                data_origin="provider",
                source_timeframe=donor_tf,
            )
            return await load_candles_pg(
                exchange="gemini",
                symbol=symbol,
                timeframe=timeframe,
                data_origin="provider",
            )

        provider = registry.get("gemini")
        df = provider.fetch_historical_candles(
            symbol=symbol, timeframe=timeframe, days=days
        )

        if df.empty:
            raise RuntimeError(
                f"Gemini returned no historical candles for {symbol} ({timeframe}); "
                "synthetic data was not substituted."
            )

        await save_candles_pg(
            exchange="gemini",
            symbol=symbol,
            timeframe=timeframe,
            df=df,
            data_origin="provider",
            source_timeframe=timeframe,
        )
        await cls.resample_and_upsert(
            exchange="gemini",
            symbol=symbol,
            base_timeframe=timeframe,
            df=df,
            data_origin="provider",
            source_timeframe=timeframe,
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
    async def resample_and_upsert(
        cls,
        exchange: str,
        symbol: str,
        df: pd.DataFrame,
        base_timeframe: str = "5m",
        target_tfs: list[str] | None = None,
        data_origin: str = "unknown",
        source_timeframe: str | None = None,
    ) -> int:
        """
        Resample base OHLCV candles into higher timeframes and upsert to PostgreSQL.

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

        has_row_provenance = {"data_origin", "source_timeframe"} <= set(df.columns)
        df_temp = df.copy()
        try:
            df_temp["timestamp"] = pd.to_datetime(
                df_temp["timestamp"], utc=True, format="ISO8601"
            )
        except Exception:
            df_temp["timestamp"] = pd.to_datetime(df_temp["timestamp"], utc=True)

        for tf in tfs_to_build:
            resample_freq = resample_freq_map.get(tf)
            if not resample_freq:
                continue

            if has_row_provenance:
                df_temp["_bucket"] = df_temp["timestamp"].dt.floor(resample_freq)
                bucket_provenance = df_temp.groupby("_bucket", sort=False).apply(
                    lambda bucket: set(
                        zip(
                            bucket["data_origin"].astype(str),
                            bucket["source_timeframe"].fillna("").astype(str),
                            strict=True,
                        )
                    )
                )
            else:
                bucket_provenance = None

            source_df = df_temp.drop(
                columns=["data_origin", "source_timeframe", "_bucket"],
                errors="ignore",
            ).set_index("timestamp")
            resampled = (
                source_df.resample(resample_freq)
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

            if resampled.empty:
                continue

            if bucket_provenance is not None:
                provenance = [
                    bucket_provenance.get(timestamp, set())
                    for timestamp in resampled["timestamp"]
                ]

                def resolved_origin(values: set[tuple[str, str]]) -> str:
                    return next(iter(values))[0] if len(values) == 1 else "unknown"

                def resolved_timeframe(values: set[tuple[str, str]]) -> str:
                    if len(values) != 1:
                        return "mixed"
                    source_tf = next(iter(values))[1]
                    return source_tf or base_timeframe

                resampled["data_origin"] = [resolved_origin(p) for p in provenance]
                resampled["source_timeframe"] = [
                    resolved_timeframe(p) for p in provenance
                ]
                groups = resampled.groupby(
                    ["data_origin", "source_timeframe"], sort=False
                )
            else:
                groups = [
                    (
                        (data_origin, source_timeframe or base_timeframe),
                        resampled,
                    )
                ]

            for (row_origin, row_source_timeframe), rows in groups:
                count = await save_candles_pg(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=tf,
                    df=rows,
                    data_origin=str(row_origin),
                    source_timeframe=str(row_source_timeframe),
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
    async def _fetch_and_store(
        cls,
        exchange: str,
        symbol: str,
        timeframe: str,
        days: int | None = None,
        limit: int | None = None,
        donor_tf: str | None = None,
        target_tfs: list[str] | None = None,
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
            df = await cls._fetch_and_store(
                exchange=exchange,
                symbol=symbol,
                timeframe=donor_tf,
                days=days,
                limit=limit,
                donor_tf=None,
                target_tfs=target_tfs,
            )
            origin = (
                "synthetic"
                if exchange_lower in {"synthetic", "synthetic_stock"}
                else "provider"
            )
            return await cls.resample_and_upsert(
                exchange=exchange_lower,
                symbol=symbol,
                df=df,
                base_timeframe=donor_tf,
                target_tfs=target_tfs or [timeframe],
                data_origin=origin,
                source_timeframe=donor_tf,
            )

        # Direct fetch path
        if exchange_lower == "kucoin":
            df = await cls.fetch_kucoin_historical(
                symbol=symbol, timeframe=timeframe, days=days
            )
            origin = "provider"
        elif exchange_lower == "gemini":
            df = await cls.fetch_gemini_historical(
                symbol=symbol, timeframe=timeframe, days=days
            )
            origin = "provider"
        elif exchange_lower in {"synthetic", "synthetic_stock"}:
            df = cls.generate_synthetic_candles(
                symbol=symbol, timeframe=timeframe, num_bars=days * tf_per_day
            )
            origin = "synthetic"
        else:
            raise ValueError(f"Unsupported candle exchange: {exchange}")

        if df.empty and origin == "provider":
            raise RuntimeError(
                f"{exchange} returned no historical candles for "
                f"{symbol} ({timeframe}); data was not synthesized."
            )

        if not df.empty:
            await save_candles_pg(
                exchange=exchange_lower,
                symbol=symbol,
                timeframe=timeframe,
                df=df,
                data_origin=origin,
                source_timeframe=timeframe,
            )
            if exchange_lower == "kucoin":
                await cls.resample_and_upsert(
                    exchange=exchange_lower,
                    symbol=symbol,
                    base_timeframe=timeframe,
                    df=df,
                    target_tfs=target_tfs,
                    data_origin=origin,
                    source_timeframe=timeframe,
                )

        if limit and len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)

        return df

    @classmethod
    async def load_candles(
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

        spec = get_timeframe_spec(timeframe, "crypto")
        tf_per_day = spec.bars_per_day_crypto

        if days is not None:
            req_candles = int(float(days) * float(tf_per_day))
            target_limit = max(int(limit or 0), req_candles)
        else:
            target_limit = limit or 500

        cached_df = pd.DataFrame()
        # Check local DB cache (exchanges only; synthetic is always generated fresh)
        if use_cache and exchange_lower not in ("synthetic", "synthetic_stock"):
            cached_df = await load_candles_pg(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=target_limit,
                data_origin="provider",
            )
            if len(cached_df) >= target_limit:
                if cls._cache_is_fresh(cached_df, timeframe):
                    print(
                        f"[DataLoader] Loaded {len(cached_df)} candles for {symbol} ({exchange}) from DB."
                    )
                    return cached_df
                else:
                    print(
                        f"[DataLoader] DB cache for {symbol} ({exchange}, {timeframe}) is stale. "
                        f"Fetching fresh data from exchange."
                    )

        if exchange_lower == "kucoin" and timeframe.lower() == "3m":
            donor_limit = max(
                int(limit or 0),
                int(float(days) * 1440) if days is not None else int(limit or 500),
            )
            donor_df = (
                await load_candles_pg(
                    exchange=exchange_lower,
                    symbol=symbol,
                    timeframe="1m",
                    limit=donor_limit,
                    data_origin="provider",
                    include_provenance=True,
                )
                if use_cache
                else pd.DataFrame()
            )
            donor_is_current = len(donor_df) >= donor_limit and cls._cache_is_fresh(
                donor_df, "1m"
            )
            donor_fetched = not donor_is_current
            if donor_fetched:
                donor_df = await cls._fetch_and_store(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe="1m",
                    days=days,
                    limit=donor_limit,
                    target_tfs=["3m"],
                )

            target_df = await load_candles_pg(
                exchange=exchange_lower,
                symbol=symbol,
                timeframe=timeframe,
                limit=target_limit,
                data_origin="provider",
            )
            if (
                donor_is_current
                and not donor_df.empty
                and len(target_df) < target_limit
            ):
                await cls.resample_and_upsert(
                    exchange=exchange_lower,
                    symbol=symbol,
                    df=donor_df,
                    base_timeframe="1m",
                    target_tfs=["3m"],
                )
                target_df = await load_candles_pg(
                    exchange=exchange_lower,
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=target_limit,
                    data_origin="provider",
                )
            return target_df if not target_df.empty else cached_df

        if donor_tf is not None and exchange_lower not in (
            "synthetic",
            "synthetic_stock",
        ):
            # Donor-fetch memoization (ema_vwap-qx8o): batch sweeps repeatedly hit the
            # same (exchange, symbol, donor_tf, limit, days) combination; skip the
            # redundant recursive fetch + resample when done recently.
            donor_key = (exchange_lower, str(symbol), donor_tf, int(limit or 0), days)
            now = time.time()
            cached_ts = cls._donor_fetch_cache.get(donor_key)
            if cached_ts is None or (now - cached_ts) >= cls._DONOR_CACHE_TTL:
                await cls.load_candles(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=donor_tf,
                    limit=limit,
                    days=days,
                    use_cache=use_cache,
                )
                donor_df = await load_candles_pg(
                    exchange=exchange_lower,
                    symbol=symbol,
                    timeframe=donor_tf,
                    data_origin="provider",
                    include_provenance=True,
                )
                if not donor_df.empty:
                    await cls.resample_and_upsert(
                        exchange=exchange_lower,
                        symbol=symbol,
                        df=donor_df,
                        base_timeframe=donor_tf,
                        target_tfs=[timeframe],
                        data_origin="provider",
                        source_timeframe=donor_tf,
                    )
                cls._donor_fetch_cache[donor_key] = now

        # Fetch fresh data using shared method (ema_vwap-plkq)
        df = await cls._fetch_and_store(
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
    async def fetch_and_update_candles(
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
        if exchange_lower in ("synthetic", "synthetic_stock"):
            return cls.generate_synthetic_candles(
                symbol=symbol, timeframe=timeframe, num_bars=limit
            )

        # Use shared fetch + store method (ema_vwap-plkq)
        try:
            df = await cls._fetch_and_store(
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
        df = await load_candles_pg(
            exchange=exchange_lower,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            data_origin="provider",
        )
        return df

    @classmethod
    def load_candles_sync(
        cls,
        exchange: str = "kucoin",
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        limit: int = 500,
        days: int | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Synchronous wrapper for load_candles. Handles running in threads.

        Uses a long run_async timeout (600s): multi-year candle loads
        (e.g. 365d of 1m = ~525k bars, live fetch + PG persist) legitimately
        take minutes; the 30s default produced bare TimeoutErrors -> HTTP 500
        on /api/walk_forward and /api/strategy/validate.
        """
        from src.sync_bridge import run_async

        return run_async(
            cls.load_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                days=days,
                use_cache=use_cache,
            ),
            timeout=600.0,
        )

    @classmethod
    def fetch_and_update_candles_sync(
        cls,
        exchange: str = "kucoin",
        symbol: str = "BTC/USD",
        timeframe: str = "5m",
        limit: int = 200,
    ) -> pd.DataFrame:
        """Synchronous wrapper for fetch_and_update_candles.

        Routes through run_async so the fetch+upsert coroutine runs on the
        MAIN event loop, where the asyncpg pools are bound. asyncio.run()
        here would drive pool-bound connections from a throwaway loop and
        corrupt the pool ("cannot perform operation: another operation is
        in progress"), killing the server process (ema_vwap polling crash).

        Uses a long run_async timeout (600s): live fetch + PG upsert from
        the exchange can take minutes for slow endpoints or large gaps.
        """
        from src.sync_bridge import run_async

        return run_async(
            cls.fetch_and_update_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
            ),
            timeout=600.0,
        )


# -------------------------------------------------------------------
# Phase 4 (ema_vwap-0w9): Multi-Timeframe Data Loader Framework
# -------------------------------------------------------------------
# Option A: 1m candles for regime classification, 5m (or target tf) for trading.
# The 1m data is cached after first fetch (acceptable overhead per user approval).
# Full integration: backtester passes 1m df to strategy/classify_regime().
# This is the framework hook; full data flow requires backtester integration.

# Multi-timeframe cache keys for regime classification
_multi_tf_cache: dict = {}


def fetch_1m_for_regime_classify(
    exchange: str, symbol: str, days: int = 90
) -> pd.DataFrame:
    """Fetch 1m candles (cached) for multi-timeframe regime classification (Phase 4)."""
    from src.data_loader import DataLoader
    from src.sync_bridge import run_async

    key = (exchange, symbol, "1m_regime")
    cached = _multi_tf_cache.get(key)
    if cached is not None:
        return cached.copy()

    result_df = run_async(
        DataLoader.load_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe="1m",
            limit=50000,
            days=days,
            use_cache=True,
        ),
        timeout=300.0,
    )
    if result_df is not None and not result_df.empty:
        _multi_tf_cache[key] = result_df.copy()
    return result_df.copy() if result_df is not None else pd.DataFrame()
