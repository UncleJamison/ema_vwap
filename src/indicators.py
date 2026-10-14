"""
Technical Indicators Engine for EMA + VWAP Strategy.
Computes vectorized EMA, VWAP (Session-based), VWAP slope, ATR, and Relative Volume metrics.
"""

import logging

import numpy as np
import pandas as pd

from src.config import StrategyParams


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average (EMA)."""
    return series.ewm(span=period, adjust=False).mean()


def compute_vwap(
    df: pd.DataFrame,
    anchor: str | None = "D",
    asset_type: str = "crypto",
) -> pd.Series:
    """
    Compute Volume Weighted Average Price (VWAP).
    - For asset_type='stock' or anchor in ('stock', 'US_EQUITY', 'NYSE', 'session'):
      resets daily at 09:30 AM US/Eastern on trading days.
    - For asset_type='crypto' or anchor in ('D', 'UTC'):
      resets daily at 00:00:00 UTC midnight.
    - If anchor is None or empty:
      computes cumulative VWAP across the entire dataframe without resetting.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = typical_price * df["volume"]

    if anchor and "timestamp" in df.columns and not df.empty:
        try:
            from src.providers.timezone import get_session_id

            is_stock = asset_type.lower() in ("stock", "equity") or str(
                anchor
            ).lower() in ("stock", "us_equity", "nyse", "nasdaq", "session")

            session_ids = get_session_id(
                df["timestamp"], asset_type="stock" if is_stock else "crypto"
            )
            cum_tpv = tpv.groupby(session_ids).cumsum()
            cum_vol = df["volume"].groupby(session_ids).cumsum()
            vwap = cum_tpv / cum_vol.replace(0, np.nan)
            return vwap.ffill()
        except Exception:
            try:
                dates = pd.to_datetime(df["timestamp"], utc=True).dt.date
                cum_tpv = tpv.groupby(dates).cumsum()
                cum_vol = df["volume"].groupby(dates).cumsum()
                vwap = cum_tpv / cum_vol.replace(0, np.nan)
                return vwap.ffill()
            except Exception:
                logging.getLogger(__name__).debug("VWAP timestamp parse fallback used")

    # Fallback to cumulative VWAP if timestamp not parsed/anchored
    cum_tpv = tpv.cumsum()
    cum_vol = df["volume"].cumsum()
    vwap = cum_tpv / cum_vol.replace(0, np.nan)
    return vwap.ffill()


def compute_vwap_slope(vwap_series: pd.Series, lookback: int = 5) -> pd.Series:
    """
    Compute normalized slope of VWAP over lookback bars.
    Slope is represented as % change per bar over lookback period.
    """
    vwap_shift = vwap_series.shift(lookback)
    slope = (vwap_series - vwap_shift) / (vwap_shift.replace(0, np.nan) * lookback)
    return slope.fillna(0.0)


def _df_fingerprint(df: pd.DataFrame) -> tuple | None:
    """Cheap content fingerprint of an OHLCV DataFrame for memoization keys."""
    try:
        if df is None or df.empty:
            return None
        return (
            len(df),
            str(df["timestamp"].iloc[0]) if "timestamp" in df.columns else None,
            str(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else None,
            round(float(df["close"].sum()), 6) if "close" in df.columns else None,
        )
    except Exception:
        return None


_slope_bounds_memo: dict[tuple, dict[str, float]] = {}


def compute_slope_bounds_from_df(
    df: pd.DataFrame,
    timeframe: str = "5m",
    lookback: int = 5,
    min_samples: int = 100,
) -> dict[str, float]:
    """Derive VWAP slope Optuna search bounds from the actual candle data.

    Computes p01 (min) and p95 (max) of the absolute VWAP slope distribution
    for the specific symbol and timeframe already loaded in ``df``.  This means
    every new symbol gets automatically calibrated bounds on its first backtest
    or sweep rather than relying on the static per-timeframe table in config.py.

    Falls back to the static ``get_vwap_slope_bounds(timeframe)`` table when
    ``df`` has fewer than ``min_samples`` valid slope values (e.g. very short
    synthetic datasets or early warmup periods).

    Args:
        df: OHLCV DataFrame, optionally already containing a ``vwap`` column.
        timeframe: Timeframe string used only for the static-table fallback.
        lookback: Bar lookback for slope computation (default 5, matches strategy).
        min_samples: Minimum valid slope values required to use live calibration.

    Returns:
        Dict with ``vwap_slope_min`` and ``vwap_slope_max`` keys, matching the
        shape returned by ``get_vwap_slope_bounds()``.
    """
    # Memoization (ema_vwap-4dv5): batch sweeps call this repeatedly with the
    # same DataFrame; fingerprint-keyed cache avoids redundant percentile passes.
    _fp = _df_fingerprint(df)
    if _fp is not None:
        memo_key = (_fp, timeframe, lookback, min_samples)
        cached = _slope_bounds_memo.get(memo_key)
        if cached is not None:
            return dict(cached)

    from src.config import get_vwap_slope_bounds  # avoid circular at module level

    static_bounds = get_vwap_slope_bounds(timeframe)

    try:
        if df is None or df.empty:
            return static_bounds

        # Reuse existing vwap column if available; compute fresh otherwise.
        if "vwap" in df.columns:
            vwap_series = df["vwap"]
        else:
            vwap_series = compute_vwap(df, anchor="D", asset_type="crypto")

        slopes = compute_vwap_slope(vwap_series, lookback=lookback).abs()
        # Filter out zeros (warmup / flat periods) and extreme outliers (>50%)
        slopes = slopes[(slopes > 0) & (slopes < 0.5)]

        if len(slopes) < min_samples:
            logging.getLogger(__name__).debug(
                f"compute_slope_bounds_from_df: only {len(slopes)} valid slopes "
                f"for {timeframe} — using static table fallback."
            )
            _slope_bounds_memo[memo_key] = dict(static_bounds)
            return static_bounds

        p01 = float(max(np.percentile(slopes, 1), 1e-7))
        p95 = float(min(np.percentile(slopes, 95), 0.5))

        # Sanity guard: p95 must be meaningfully larger than p01
        if p95 <= p01 * 1.5:
            _slope_bounds_memo[memo_key] = dict(static_bounds)
            return static_bounds

        logging.getLogger(__name__).debug(
            f"compute_slope_bounds_from_df: {timeframe} live bounds "
            f"p01={p01:.6f} p95={p95:.6f} ({len(slopes)} samples)"
        )
        result = {"vwap_slope_min": p01, "vwap_slope_max": p95}
        _slope_bounds_memo[memo_key] = dict(result)
        return result

    except Exception:
        return static_bounds


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range (ATR)."""
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr


def compute_bollinger_bands(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute Bollinger Bands: (upper, middle, lower).

    Middle = SMA(period)
    Upper = Middle + num_std * Std(period)
    Lower = Middle - num_std * Std(period)
    """
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute Relative Strength Index (RSI).

    RSI = 100 - (100 / (1 + RS))
    where RS = Average Gain / Average Loss over period
    """
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / (loss + 1e-8)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_relative_volume(volume_series: pd.Series, period: int = 20) -> pd.Series:
    """Compute Relative Volume (Volume / SMA(Volume, period))."""
    vol_sma = volume_series.rolling(window=period, min_periods=1).mean()
    rvol = volume_series / vol_sma.replace(0, np.nan)
    return rvol.fillna(1.0)


def compute_shared_indicators(
    df: pd.DataFrame, params: StrategyParams | None = None
) -> pd.DataFrame:
    """Compute indicators that do not vary across an Optuna candidate search."""
    if params is None:
        params = StrategyParams()

    df = df.copy()
    asset_type = getattr(params, "asset_type", "crypto")
    vwap_anchor = getattr(params, "vwap_anchor", "D")
    df["vwap"] = compute_vwap(df, anchor=vwap_anchor, asset_type=asset_type)
    df["vwap_slope"] = compute_vwap_slope(
        df["vwap"], lookback=params.vwap_slope_lookback
    )
    df["atr"] = compute_atr(df, period=params.atr_period)
    df["vol_sma"] = (
        df["volume"].rolling(window=params.volume_sma_period, min_periods=1).mean()
    )
    df["rvol"] = compute_relative_volume(df["volume"], period=params.volume_sma_period)
    return df


def compute_all_indicators(
    df: pd.DataFrame, params: StrategyParams | None = None
) -> pd.DataFrame:
    """Calculate all technical indicators required by the EMA + VWAP strategy."""
    if params is None:
        params = StrategyParams()

    df = compute_shared_indicators(df, params)
    df[f"ema_{params.fast_ema}"] = compute_ema(df["close"], params.fast_ema)
    df[f"ema_{params.slow_ema}"] = compute_ema(df["close"], params.slow_ema)
    df[f"ema_{params.trend_ema}"] = compute_ema(df["close"], params.trend_ema)

    return df
