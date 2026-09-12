"""
Technical Indicators Engine for EMA + VWAP Strategy.
Computes vectorized EMA, VWAP (Session-based), VWAP slope, ATR, and Relative Volume metrics.
"""

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
                pass

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


def compute_relative_volume(volume_series: pd.Series, period: int = 20) -> pd.Series:
    """Compute Relative Volume (Volume / SMA(Volume, period))."""
    vol_sma = volume_series.rolling(window=period, min_periods=1).mean()
    rvol = volume_series / vol_sma.replace(0, np.nan)
    return rvol.fillna(1.0)


def compute_all_indicators(
    df: pd.DataFrame, params: StrategyParams | None = None
) -> pd.DataFrame:
    """
    Calculates all technical indicators required for EMA + VWAP strategy and appends them to df.
    """
    if params is None:
        params = StrategyParams()

    df = df.copy()

    # Compute EMAs
    df[f"ema_{params.fast_ema}"] = compute_ema(df["close"], params.fast_ema)
    df[f"ema_{params.slow_ema}"] = compute_ema(df["close"], params.slow_ema)
    df[f"ema_{params.trend_ema}"] = compute_ema(df["close"], params.trend_ema)

    # Determine asset type and VWAP anchor
    asset_type = getattr(params, "asset_type", "crypto")
    vwap_anchor = getattr(params, "vwap_anchor", "D")

    # Compute Session-Anchored VWAP
    df["vwap"] = compute_vwap(df, anchor=vwap_anchor, asset_type=asset_type)

    # Compute VWAP Slope
    df["vwap_slope"] = compute_vwap_slope(
        df["vwap"], lookback=params.vwap_slope_lookback
    )

    # Compute ATR
    df["atr"] = compute_atr(df, period=params.atr_period)

    # Compute Relative Volume & Volume SMA
    df["vol_sma"] = (
        df["volume"].rolling(window=params.volume_sma_period, min_periods=1).mean()
    )
    df["rvol"] = compute_relative_volume(df["volume"], period=params.volume_sma_period)

    return df
